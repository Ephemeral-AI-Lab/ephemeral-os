use crate::workspace_crate::{CallerId, WorkspaceId};
use crate::workspace_session::model::WorkspaceSessionHandler;
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

impl WorkspaceSessionService {
    pub fn resolve_session(
        &self,
        workspace_session_id: WorkspaceId,
        caller_id: CallerId,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        let sessions = self.lock_sessions()?;
        let session = sessions
            .get(&workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::not_found(&workspace_session_id))?;

        if session.handle.owner != caller_id {
            return Err(WorkspaceSessionError::CallerMismatch {
                workspace_session_id,
                expected: session.handle.owner.clone(),
                actual: caller_id,
            });
        }

        Ok(session.handler())
    }
}
