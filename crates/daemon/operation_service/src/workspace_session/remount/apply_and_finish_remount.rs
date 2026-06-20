use crate::workspace_crate::{RemountWorkspaceRequest, WorkspaceHandle, WorkspaceId};
use crate::workspace_session::{
    WorkspaceSessionError, WorkspaceSessionHandler, WorkspaceSessionService,
};

impl WorkspaceSessionService {
    fn pending_remount_handle(
        &self,
        handler: &WorkspaceSessionHandler,
    ) -> Result<WorkspaceHandle, WorkspaceSessionError> {
        let sessions = self.lock_sessions()?;
        let session = sessions
            .get(&handler.workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::not_found(&handler.workspace_session_id))?;
        if !session.remount_state.is_pending() {
            return Err(WorkspaceSessionError::RemountNotPending {
                workspace_session_id: handler.workspace_session_id.clone(),
            });
        }
        Ok(session.handle.clone())
    }

    fn commit_remount_result(
        &self,
        handler: &WorkspaceSessionHandler,
        handle: WorkspaceHandle,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .get_mut(&handler.workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::not_found(&handler.workspace_session_id))?;
        if !session.remount_state.is_pending() {
            return Err(WorkspaceSessionError::RemountNotPending {
                workspace_session_id: handler.workspace_session_id.clone(),
            });
        }
        if let Err(error) = session.refresh_from_handle(handle) {
            let _ = session.block_remount();
            return Err(error);
        }
        session.finish_remount()?;
        Ok(session.handler())
    }

    fn block_remount_if_pending(
        &self,
        workspace_session_id: &WorkspaceId,
    ) -> Result<(), WorkspaceSessionError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .get_mut(workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::not_found(workspace_session_id))?;
        if session.remount_state.is_pending() {
            session.block_remount()?;
        }
        Ok(())
    }

    pub fn apply_and_finish_remount(
        &self,
        handler: &WorkspaceSessionHandler,
        request: RemountWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        let handle = self.pending_remount_handle(handler)?;
        let result = match self.workspace().remount_workspace(&handle, request) {
            Ok(result) => result,
            Err(error) => {
                let _ = self.block_remount_if_pending(&handler.workspace_session_id);
                return Err(WorkspaceSessionError::Workspace(error));
            }
        };
        self.commit_remount_result(handler, result.handle)
    }
}
