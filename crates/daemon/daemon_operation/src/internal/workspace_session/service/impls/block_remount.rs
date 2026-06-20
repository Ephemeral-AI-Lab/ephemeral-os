use crate::workspace_crate::WorkspaceId;
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

impl WorkspaceSessionService {
    pub fn block_remount(
        &self,
        workspace_session_id: WorkspaceId,
    ) -> Result<(), WorkspaceSessionError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .get_mut(&workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::not_found(&workspace_session_id))?;

        session.block_remount()
    }
}
