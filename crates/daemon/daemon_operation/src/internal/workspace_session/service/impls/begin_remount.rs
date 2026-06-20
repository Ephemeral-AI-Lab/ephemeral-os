use crate::workspace_crate::WorkspaceSessionId;
use crate::workspace_session::{
    WorkspaceSessionError, WorkspaceSessionHandler, WorkspaceSessionService,
};

impl WorkspaceSessionService {
    pub fn begin_remount(
        &self,
        workspace_session_id: WorkspaceSessionId,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .get_mut(&workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::not_found(&workspace_session_id))?;

        session.begin_remount()
    }
}
