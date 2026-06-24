use std::collections::HashMap;
use std::sync::{Arc, Mutex, MutexGuard};

use crate::workspace_crate::{WorkspaceRuntimeService, WorkspaceSessionId};
use crate::workspace_session::WorkspaceSessionError;

use super::model::WorkspaceSession;

pub struct WorkspaceSessionService {
    sessions: Mutex<HashMap<WorkspaceSessionId, WorkspaceSession>>,
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
    ) -> Result<MutexGuard<'_, HashMap<WorkspaceSessionId, WorkspaceSession>>, WorkspaceSessionError>
    {
        self.sessions
            .lock()
            .map_err(|_| WorkspaceSessionError::LockPoisoned)
    }
}
