use crate::workspace_crate::{RemountWorkspaceRequest, WorkspaceId};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionHandler};

pub trait RemountWorkspaceSession: Send + Sync {
    fn begin_remount(
        &self,
        workspace_session_id: WorkspaceId,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError>;

    fn apply_remount(
        &self,
        handler: &WorkspaceSessionHandler,
        request: RemountWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError>;

    fn apply_and_finish_remount(
        &self,
        handler: &WorkspaceSessionHandler,
        request: RemountWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        let updated = self.apply_remount(handler, request)?;
        self.finish_remount(handler.workspace_session_id.clone())?;
        Ok(updated)
    }

    fn finish_remount(
        &self,
        workspace_session_id: WorkspaceId,
    ) -> Result<(), WorkspaceSessionError>;

    fn finish_or_block_remount(
        &self,
        workspace_session_id: WorkspaceId,
        reason: Option<String>,
    ) -> Result<(), WorkspaceSessionError>;

    fn is_remount_pending(&self, workspace_session_id: &WorkspaceId) -> bool;
}
