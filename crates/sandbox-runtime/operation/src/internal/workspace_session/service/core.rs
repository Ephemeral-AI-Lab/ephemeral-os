use std::collections::HashMap;
use std::sync::{Arc, Mutex, MutexGuard};

use crate::workspace_crate::{
    CgroupMonitorConfig, CgroupMonitorRegistry, WorkspaceRuntimeService, WorkspaceSessionId,
};
use crate::workspace_session::WorkspaceSessionError;

use super::model::WorkspaceSession;

pub struct WorkspaceSessionService {
    sessions: Mutex<HashMap<WorkspaceSessionId, WorkspaceSession>>,
    workspace: Arc<WorkspaceRuntimeService>,
    cgroup_monitor: Arc<CgroupMonitorRegistry>,
}

impl WorkspaceSessionService {
    #[must_use]
    pub fn new(workspace: Arc<WorkspaceRuntimeService>) -> Self {
        Self::with_cgroup_monitor(workspace, CgroupMonitorConfig::default())
    }

    #[must_use]
    pub fn with_cgroup_monitor(
        workspace: Arc<WorkspaceRuntimeService>,
        cgroup_monitor: CgroupMonitorConfig,
    ) -> Self {
        Self {
            sessions: Mutex::new(HashMap::new()),
            workspace,
            cgroup_monitor: Arc::new(CgroupMonitorRegistry::new(cgroup_monitor)),
        }
    }

    #[must_use]
    pub(crate) fn workspace(&self) -> &Arc<WorkspaceRuntimeService> {
        &self.workspace
    }

    #[must_use]
    pub fn cgroup_monitor(&self) -> Arc<CgroupMonitorRegistry> {
        Arc::clone(&self.cgroup_monitor)
    }

    pub(crate) fn lock_sessions(
        &self,
    ) -> Result<MutexGuard<'_, HashMap<WorkspaceSessionId, WorkspaceSession>>, WorkspaceSessionError>
    {
        self.sessions
            .lock()
            .map_err(|_| WorkspaceSessionError::LockPoisoned)
    }

    #[must_use]
    pub fn is_remount_pending(&self, workspace_session_id: &WorkspaceSessionId) -> bool {
        self.lock_sessions().is_ok_and(|sessions| {
            sessions
                .get(workspace_session_id)
                .is_some_and(|session| session.remount_state.is_pending())
        })
    }

    #[must_use]
    pub fn is_remount_blocked(&self, workspace_session_id: &WorkspaceSessionId) -> bool {
        self.lock_sessions().is_ok_and(|sessions| {
            sessions
                .get(workspace_session_id)
                .is_some_and(|session| session.remount_state.is_blocked())
        })
    }
}
