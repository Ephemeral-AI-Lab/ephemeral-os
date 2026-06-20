use std::collections::HashMap;
use std::sync::{Arc, Mutex, MutexGuard};

use crate::workspace_crate::{WorkspaceId, WorkspaceRuntimeService};
use crate::workspace_session::model::WorkspaceSession;
use crate::workspace_session::WorkspaceSessionError;

pub(crate) type WorkspaceSessions = HashMap<WorkspaceId, WorkspaceSession>;

pub struct WorkspaceSessionService {
    sessions: Mutex<WorkspaceSessions>,
    workspace: Arc<WorkspaceRuntimeService>,
}

impl WorkspaceSessionService {
    #[must_use]
    pub fn new(workspace: Arc<WorkspaceRuntimeService>) -> Self {
        Self {
            sessions: Mutex::new(HashMap::new()),
            workspace,
        }
    }

    #[must_use]
    pub(crate) fn workspace(&self) -> &Arc<WorkspaceRuntimeService> {
        &self.workspace
    }

    pub(crate) fn lock_sessions(
        &self,
    ) -> Result<MutexGuard<'_, WorkspaceSessions>, WorkspaceSessionError> {
        self.sessions
            .lock()
            .map_err(|_| WorkspaceSessionError::LockPoisoned)
    }

    #[must_use]
    pub fn is_remount_pending(&self, workspace_session_id: &WorkspaceId) -> bool {
        self.lock_sessions().is_ok_and(|sessions| {
            sessions
                .get(workspace_session_id)
                .is_some_and(|session| session.remount_state.is_pending())
        })
    }
}
