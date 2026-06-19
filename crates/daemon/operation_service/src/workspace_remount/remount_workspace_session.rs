use crate::workspace_crate::{RemountWorkspaceRequest, WorkspaceId};
use crate::workspace_remount::RemountWorkspaceSession;
use crate::workspace_session::{
    WorkspaceRemountState, WorkspaceSessionError, WorkspaceSessionHandler, WorkspaceSessionService,
};

impl RemountWorkspaceSession for WorkspaceSessionService {
    fn begin_remount(
        &self,
        workspace_session_id: WorkspaceId,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .find_by_workspace_session_id_mut(&workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::NotFound {
                workspace_session_id: workspace_session_id.clone(),
            })?;

        session.begin_remount()
    }

    fn apply_remount(
        &self,
        handler: &WorkspaceSessionHandler,
        request: RemountWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .find_by_workspace_session_id_mut(&handler.workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::NotFound {
                workspace_session_id: handler.workspace_session_id.clone(),
            })?;
        session.ensure_active()?;
        if !matches!(session.remount_state, WorkspaceRemountState::RemountPending) {
            return Err(WorkspaceSessionError::RemountNotPending {
                workspace_session_id: handler.workspace_session_id.clone(),
            });
        }

        let result = match self.workspace().remount_workspace(&session.handle, request) {
            Ok(result) => result,
            Err(error) => {
                let reason = error.to_string();
                let _ = session.block_remount(reason);
                return Err(WorkspaceSessionError::Workspace(error));
            }
        };
        if let Err(error) = session.refresh_from_handle(result.handle) {
            let reason = error.to_string();
            let _ = session.block_remount(reason);
            return Err(error);
        }
        Ok(session.handler())
    }

    fn finish_remount(
        &self,
        workspace_session_id: WorkspaceId,
    ) -> Result<(), WorkspaceSessionError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .find_by_workspace_session_id_mut(&workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::NotFound {
                workspace_session_id: workspace_session_id.clone(),
            })?;

        session.finish_remount()
    }

    fn finish_or_block_remount(
        &self,
        workspace_session_id: WorkspaceId,
        reason: Option<String>,
    ) -> Result<(), WorkspaceSessionError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .find_by_workspace_session_id_mut(&workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::NotFound {
                workspace_session_id: workspace_session_id.clone(),
            })?;

        match reason {
            Some(reason) => session.block_remount(reason),
            None => session.finish_remount(),
        }
    }

    fn is_remount_pending(&self, workspace_session_id: &WorkspaceId) -> bool {
        WorkspaceSessionService::is_remount_pending(self, workspace_session_id)
    }
}
