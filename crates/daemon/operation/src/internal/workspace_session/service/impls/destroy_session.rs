use crate::workspace_crate::{DestroyWorkspaceRequest, DestroyWorkspaceResult};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

use super::super::model::WorkspaceSessionHandler;

impl WorkspaceSessionService {
    pub fn destroy_session(
        &self,
        handler: WorkspaceSessionHandler,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceSessionError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .get(&handler.workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::not_found(&handler.workspace_session_id))?;
        let handle = session.active_handle()?;

        match self.workspace().destroy_workspace(handle, request) {
            Ok(result) => {
                sessions.remove(&handler.workspace_session_id);
                Ok(result)
            }
            Err(error) => Err(WorkspaceSessionError::Workspace(error)),
        }
    }
}
