use std::sync::{Arc, Mutex, MutexGuard};

use crate::workspace_crate::{WorkspaceId, WorkspaceRuntimeService};
use crate::workspace_session::model::WorkspaceRemountState;
use crate::workspace_session::session_store::WorkspaceSessionStore;
use crate::workspace_session::WorkspaceSessionError;

pub struct WorkspaceSessionService {
    sessions: Mutex<WorkspaceSessionStore>,
    workspace: Arc<WorkspaceRuntimeService>,
}

impl WorkspaceSessionService {
    #[must_use]
    pub fn new(workspace: Arc<WorkspaceRuntimeService>) -> Self {
        Self {
            sessions: Mutex::new(WorkspaceSessionStore::default()),
            workspace,
        }
    }

    #[must_use]
    pub(crate) fn workspace(&self) -> &Arc<WorkspaceRuntimeService> {
        &self.workspace
    }

    pub(crate) fn lock_sessions(
        &self,
    ) -> Result<MutexGuard<'_, WorkspaceSessionStore>, WorkspaceSessionError> {
        self.sessions
            .lock()
            .map_err(|_| WorkspaceSessionError::LockPoisoned)
    }

    #[must_use]
    pub fn is_remount_pending(&self, workspace_session_id: &WorkspaceId) -> bool {
        self.lock_sessions().is_ok_and(|sessions| {
            sessions
                .find_by_workspace_session_id(workspace_session_id)
                .is_some_and(|session| {
                    matches!(session.remount_state, WorkspaceRemountState::RemountPending)
                })
        })
    }
}
