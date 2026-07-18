use crate::workspace_crate::WorkspaceSessionId;
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

use super::super::model::WorkspaceSessionHandler;

impl WorkspaceSessionService {
    pub fn resolve_session(
        &self,
        workspace_session_id: WorkspaceSessionId,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        let sessions = self.lock_sessions()?;
        let session = sessions
            .get(&workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::not_found(&workspace_session_id))?;

        if !session.handle.holder_is_live() {
            return Err(WorkspaceSessionError::HolderExited {
                workspace_session_id,
                reason: session
                    .handle
                    .holder_exit_reason()
                    .unwrap_or_else(|| "exit-status:unknown".to_owned()),
                cleanup_state: session.finalization_state,
            });
        }

        Ok(session.handler())
    }
}
