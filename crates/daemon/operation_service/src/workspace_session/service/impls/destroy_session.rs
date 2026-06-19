use crate::workspace_crate::{DestroyWorkspaceRequest, DestroyWorkspaceResult};
use crate::workspace_session::model::WorkspaceSessionHandler;
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

impl WorkspaceSessionService {
    pub fn destroy_session(
        &self,
        handler: WorkspaceSessionHandler,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceSessionError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .find_by_workspace_session_id_mut(&handler.workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::NotFound {
                workspace_session_id: handler.workspace_session_id.clone(),
            })?;
        let handle = session.mark_closing()?;

        match self.workspace().destroy_workspace(handle, request) {
            Ok(result) => {
                sessions.remove(&handler.workspace_session_id);
                Ok(result)
            }
            Err(error) => {
                if let Some(session) =
                    sessions.find_by_workspace_session_id_mut(&handler.workspace_session_id)
                {
                    session.mark_active();
                }
                Err(WorkspaceSessionError::Workspace(error))
            }
        }
    }
}
