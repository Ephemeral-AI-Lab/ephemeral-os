use crate::workspace_crate::{CreateWorkspaceRequest, DestroyWorkspaceRequest};
use crate::workspace_session::model::{WorkspaceSession, WorkspaceSessionHandler};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

impl WorkspaceSessionService {
    pub fn create_workspace_session(
        &self,
        request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        let layer_stack_root = request.layer_stack_root.clone();
        let handle = self.workspace().create_workspace(request)?;
        let workspace_session_id = handle.id.clone();
        let session = WorkspaceSession::from_handle(handle.clone(), layer_stack_root);
        let handler = session.handler();

        let insert_result = self
            .lock_sessions()
            .and_then(|mut sessions| sessions.insert(session));
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
            return Err(insert_error);
        }

        Ok(handler)
    }
}
