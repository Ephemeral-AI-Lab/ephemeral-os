use std::sync::PoisonError;

use sandbox_observability::record::names;

use crate::workspace_crate::{RemountOutcome, WorkspaceSessionId};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

/// One session's disposition in the post-commit remount sweep, plus the
/// pre-attempt manifest layer ids that attribute it to squashed blocks.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SweptSession {
    pub workspace_session_id: WorkspaceSessionId,
    pub pre_manifest_layer_ids: Vec<String>,
    pub disposition: SweptDisposition,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SweptDisposition {
    SessionGone,
    Identity,
    Migrated,
    Leased { reason: String },
    Faulty { class_detail: String },
}

impl WorkspaceSessionService {
    /// Attempt the live remount of one session under its admission gate.
    /// Session-not-found at the gate is a silent skip; after a verified
    /// switch (migrated or parked) the registry copy of the handle is
    /// refreshed from the workspace runtime.
    pub fn remount_session(&self, workspace_session_id: &WorkspaceSessionId) -> SweptSession {
        let result: Result<SweptSession, std::convert::Infallible> =
            self.obs().scope(names::WORKSPACE_SESSION_REMOUNT, |span| {
                span.attr("workspace_session_id", workspace_session_id.0.clone());
                let gate = self.session_gate(workspace_session_id);
                let _admission = gate.lock().unwrap_or_else(PoisonError::into_inner);
                let (pre_manifest_layer_ids, cgroup_procs_path) = {
                    let Ok(sessions) = self.lock_sessions() else {
                        return Ok(swept(
                            workspace_session_id,
                            Vec::new(),
                            SweptDisposition::Leased {
                                reason: "mount_uncertain:session_registry_unavailable".to_owned(),
                            },
                        ));
                    };
                    let Some(session) = sessions.get(workspace_session_id) else {
                        return Ok(swept(
                            workspace_session_id,
                            Vec::new(),
                            SweptDisposition::SessionGone,
                        ));
                    };
                    (
                        session
                            .handle
                            .snapshot
                            .manifest
                            .layers
                            .iter()
                            .map(|layer| layer.layer_id.clone())
                            .collect(),
                        session
                            .cgroup_path
                            .as_ref()
                            .map(|cgroup| cgroup.join("cgroup.procs")),
                    )
                };
                let outcome = self
                    .workspace()
                    .remount_workspace(workspace_session_id, cgroup_procs_path);
                let disposition = match outcome {
                    Ok(None) => SweptDisposition::SessionGone,
                    Ok(Some(RemountOutcome::Identity)) => SweptDisposition::Identity,
                    Ok(Some(RemountOutcome::Migrated { .. })) => {
                        self.refresh_session_handle(workspace_session_id);
                        SweptDisposition::Migrated
                    }
                    Ok(Some(RemountOutcome::Leased { reason })) => {
                        if reason == "pinned:rollback_unmount_busy" {
                            self.refresh_session_handle(workspace_session_id);
                        }
                        SweptDisposition::Leased { reason }
                    }
                    Ok(Some(RemountOutcome::Faulty { class_detail })) => {
                        SweptDisposition::Faulty { class_detail }
                    }
                    Err(error) => SweptDisposition::Leased {
                        reason: format!("mount_uncertain:remount_transaction:{error}"),
                    },
                };
                span.attr("disposition", format!("{disposition:?}"));
                Ok(swept(
                    workspace_session_id,
                    pre_manifest_layer_ids,
                    disposition,
                ))
            });
        match result {
            Ok(swept_session) => swept_session,
            Err(never) => match never {},
        }
    }

    /// Destroy a faulty session through the ordinary destroy path (still
    /// under its gate) and report the lease-release errors for the result
    /// line. The session's frozen tasks die with the namespace.
    pub fn destroy_faulty_session(&self, workspace_session_id: &WorkspaceSessionId) -> Vec<String> {
        let gate = self.session_gate(workspace_session_id);
        let _admission = gate.lock().unwrap_or_else(PoisonError::into_inner);
        let handler = match self.resolve_session(workspace_session_id.clone()) {
            Ok(handler) => handler,
            Err(WorkspaceSessionError::NotFound { .. }) => return Vec::new(),
            Err(error) => return vec![format!("resolve for destroy: {error}")],
        };
        match self.destroy_session(
            handler,
            crate::workspace_crate::DestroyWorkspaceRequest::default(),
        ) {
            Ok(result) => result
                .lease_release_error
                .map(|error| vec![error])
                .unwrap_or_default(),
            Err(error) => vec![format!("destroy: {error}")],
        }
    }

    fn refresh_session_handle(&self, workspace_session_id: &WorkspaceSessionId) {
        let refreshed = self
            .workspace()
            .current_handle(workspace_session_id)
            .ok()
            .flatten();
        let Some(handle) = refreshed else {
            return;
        };
        if let Ok(mut sessions) = self.lock_sessions() {
            if let Some(session) = sessions.get_mut(workspace_session_id) {
                session.handle = handle;
            }
        }
    }
}

fn swept(
    workspace_session_id: &WorkspaceSessionId,
    pre_manifest_layer_ids: Vec<String>,
    disposition: SweptDisposition,
) -> SweptSession {
    SweptSession {
        workspace_session_id: workspace_session_id.clone(),
        pre_manifest_layer_ids,
        disposition,
    }
}
