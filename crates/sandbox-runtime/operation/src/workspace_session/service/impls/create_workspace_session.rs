use std::collections::hash_map::Entry;

use sandbox_observability_telemetry::record::names;
use serde_json::json;

use crate::workspace_crate::{
    CreateWorkspaceRequest, DestroyWorkspaceRequest, DestroyWorkspaceResult, WorkspaceHandle,
};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

use super::super::cgroup::cleanup_workspace_cgroup;
use super::super::model::{
    CreateSessionRequest, FinalizationState, FinalizePolicy, WorkspaceSession,
    WorkspaceSessionHandler,
};

impl WorkspaceSessionService {
    pub fn create_workspace_session(
        &self,
        request: CreateSessionRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        self.obs().scope(names::WORKSPACE_SESSION_CREATE, |span| {
            span.attr("finalize_policy", request.finalize_policy.as_str());
            let workspace_session_id = self
                .workspace()
                .allocate_workspace_session_id(request.network)?;
            let _reservation = self.reserve_workspace_session_id(workspace_session_id.clone())?;
            let handle = self.workspace().create_workspace(CreateWorkspaceRequest {
                workspace_session_id: workspace_session_id.clone(),
                network: request.network,
            })?;
            if handle.id != workspace_session_id {
                return Err(WorkspaceSessionError::WorkspaceIdentityMismatch {
                    reserved_workspace_session_id: workspace_session_id,
                    returned_workspace_session_id: handle.id,
                });
            }
            let cgroup_path = match self.prepare_workspace_cgroup(&workspace_session_id) {
                Ok(path) => path,
                Err(error) => {
                    let raw_rollback = self
                        .workspace()
                        .destroy_workspace(handle.clone(), DestroyWorkspaceRequest::default());
                    let cgroup_path = self.workspace_cgroup_path(&workspace_session_id);
                    let cgroup_retry = setup_rollback_failed(&error)
                        && cgroup_path.as_ref().is_some_and(|path| path.exists());
                    if raw_rollback.is_err() || cgroup_retry {
                        return Err(self.retain_failed_create_cleanup(
                            handle,
                            cgroup_path.filter(|_| cgroup_retry),
                            request.finalize_policy,
                            raw_rollback,
                            error,
                        ));
                    }
                    return Err(error);
                }
            };
            let session = WorkspaceSession::from_handle(
                handle.clone(),
                cgroup_path.clone(),
                request.finalize_policy,
            );
            let handler = session.handler();

            let insert_result = self.lock_sessions().and_then(|mut sessions| {
                match sessions.entry(workspace_session_id.clone()) {
                    Entry::Vacant(entry) => {
                        entry.insert(session);
                        Ok(())
                    }
                    Entry::Occupied(_) => Err(WorkspaceSessionError::DuplicateWorkspaceSessionId {
                        workspace_session_id: workspace_session_id.clone(),
                    }),
                }
            });

            if let Err(insert_error) = insert_result {
                if let Err(rollback_error) = self
                    .workspace()
                    .destroy_workspace(handle, DestroyWorkspaceRequest::default())
                {
                    return Err(WorkspaceSessionError::CreateRollbackFailed {
                        workspace_session_id,
                        insert_error: Box::new(insert_error),
                        rollback_error,
                    });
                }
                if let Some(cgroup_path) = &cgroup_path {
                    let _ = cleanup_workspace_cgroup(cgroup_path);
                }
                return Err(insert_error);
            }

            self.obs().event(
                names::LEASE_ACQUIRED,
                json!({ "revision": handler.handle.base_revision().version }),
            );
            // Close the only startup race: a holder may exit after the raw
            // workspace is created but before its operation session is
            // inserted. A queued wake can legitimately run early and see no
            // session, so creation performs one bounded reconciliation after
            // publication. Later exits wake the blocking dispatcher.
            let _ = self.reconcile_holder_exits();
            Ok(handler)
        })
    }
}

impl WorkspaceSessionService {
    fn retain_failed_create_cleanup(
        &self,
        handle: WorkspaceHandle,
        cgroup_path: Option<std::path::PathBuf>,
        finalize_policy: FinalizePolicy,
        raw_rollback: Result<DestroyWorkspaceResult, crate::workspace_crate::WorkspaceError>,
        setup_error: WorkspaceSessionError,
    ) -> WorkspaceSessionError {
        let workspace_session_id = handle.id.clone();
        let mut session = WorkspaceSession::from_handle(handle, cgroup_path, finalize_policy);
        session.finalization_state = FinalizationState::FinalizeFailed;
        let mut failures = vec![format!("workspace setup: {setup_error}")];
        match raw_rollback {
            Ok(result) => session.workspace_destroy_result = Some(result),
            Err(error) => failures.push(format!("workspace rollback: {error}")),
        }
        if !session.cgroup_cleanup_complete {
            failures.push("workload-cgroup rollback remains retryable".to_owned());
        }

        match self.lock_sessions() {
            Ok(mut sessions) => match sessions.entry(workspace_session_id.clone()) {
                Entry::Vacant(entry) => {
                    entry.insert(session);
                }
                Entry::Occupied(_) => {
                    failures.push(
                        "cleanup handle could not be retained because the identity is active"
                            .to_owned(),
                    );
                }
            },
            Err(error) => failures.push(format!("retain cleanup handle: {error}")),
        }
        WorkspaceSessionError::TeardownIncomplete {
            workspace_session_id,
            failures,
        }
    }
}

fn setup_rollback_failed(error: &WorkspaceSessionError) -> bool {
    matches!(
        error,
        WorkspaceSessionError::WorkloadCgroupSetupFailed {
            rollback_diagnostic: Some(_),
            ..
        }
    )
}
