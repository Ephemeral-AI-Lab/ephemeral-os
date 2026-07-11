use std::path::PathBuf;

use sandbox_observability_telemetry::record::names;
use serde_json::json;

use crate::workspace_crate::{
    DestroyWorkspaceRequest, DestroyWorkspaceResult, WorkspaceHandle, WorkspaceSessionId,
};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

use super::super::model::WorkspaceSessionHandler;

/// The state a destroy needs, snapshotted under a brief `sessions` lock so the
/// lock is never held across the workspace teardown I/O (§2.3 hard rule).
pub(crate) struct DestroySnapshot {
    pub(crate) workspace_session_id: WorkspaceSessionId,
    pub(crate) handle: WorkspaceHandle,
    pub(crate) cgroup_path: Option<PathBuf>,
}

impl WorkspaceSessionService {
    pub fn destroy_session(
        &self,
        handler: WorkspaceSessionHandler,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceSessionError> {
        self.obs().scope(names::WORKSPACE_SESSION_DESTROY, |_span| {
            let snapshot = self.snapshot_for_destroy(&handler.workspace_session_id)?;
            self.destroy_snapshot(snapshot, request)
        })
    }

    pub(crate) fn snapshot_for_destroy(
        &self,
        workspace_session_id: &WorkspaceSessionId,
    ) -> Result<DestroySnapshot, WorkspaceSessionError> {
        let sessions = self.lock_sessions()?;
        let session = sessions
            .get(workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::not_found(workspace_session_id))?;
        Ok(DestroySnapshot {
            workspace_session_id: session.workspace_session_id.clone(),
            handle: session.handle.clone(),
            cgroup_path: session.cgroup_path.clone(),
        })
    }

    pub(crate) fn destroy_snapshot(
        &self,
        snapshot: DestroySnapshot,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceSessionError> {
        let DestroySnapshot {
            workspace_session_id,
            handle,
            cgroup_path,
        } = snapshot;
        let revision = handle.base_revision().version;
        match self.workspace().destroy_workspace(handle, request) {
            Ok(result) => {
                if let Ok(mut sessions) = self.lock_sessions() {
                    sessions.remove(&workspace_session_id);
                }
                self.drop_session_gate(&workspace_session_id);
                if let Some(cgroup_path) = &cgroup_path {
                    let _ = std::fs::remove_dir(cgroup_path);
                }
                self.obs()
                    .event(names::LEASE_RELEASED, json!({ "revision": revision }));
                Ok(result)
            }
            Err(error) => Err(WorkspaceSessionError::Workspace(error)),
        }
    }
}
