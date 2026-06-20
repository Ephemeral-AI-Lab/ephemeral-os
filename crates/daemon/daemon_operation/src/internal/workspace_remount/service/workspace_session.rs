use crate::workspace_crate::{RemountWorkspaceRequest, WorkspaceSessionId};
use crate::workspace_session::{
    WorkspaceSessionError, WorkspaceSessionHandler, WorkspaceSessionService,
};

pub trait RemountWorkspaceSession: Send + Sync {
    fn begin_remount(
        &self,
        workspace_session_id: WorkspaceSessionId,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError>;

    fn apply_and_finish_remount(
        &self,
        handler: &WorkspaceSessionHandler,
        request: RemountWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError>;

    fn block_remount(
        &self,
        workspace_session_id: WorkspaceSessionId,
    ) -> Result<(), WorkspaceSessionError>;
}

impl RemountWorkspaceSession for WorkspaceSessionService {
    fn begin_remount(
        &self,
        workspace_session_id: WorkspaceSessionId,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        WorkspaceSessionService::begin_remount(self, workspace_session_id)
    }

    fn apply_and_finish_remount(
        &self,
        handler: &WorkspaceSessionHandler,
        request: RemountWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        WorkspaceSessionService::apply_and_finish_remount(self, handler, request)
    }

    fn block_remount(
        &self,
        workspace_session_id: WorkspaceSessionId,
    ) -> Result<(), WorkspaceSessionError> {
        WorkspaceSessionService::block_remount(self, workspace_session_id)
    }
}
