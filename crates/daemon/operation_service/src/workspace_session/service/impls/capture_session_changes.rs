use crate::workspace_crate::{CaptureChangesRequest, CapturedWorkspaceChanges};
use crate::workspace_session::model::WorkspaceSessionHandler;
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

impl WorkspaceSessionService {
    pub fn capture_session_changes(
        &self,
        handler: &WorkspaceSessionHandler,
        request: CaptureChangesRequest,
    ) -> Result<CapturedWorkspaceChanges, WorkspaceSessionError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .find_by_workspace_session_id_mut(&handler.workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::NotFound {
                workspace_session_id: handler.workspace_session_id.clone(),
            })?;
        let handle = session.active_handle()?;
        let result = self.workspace().capture_changes(&handle, request)?;
        session.refresh_after_capture(result.base_revision.clone());

        Ok(result)
    }
}
