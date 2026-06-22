use std::collections::HashMap;
use std::sync::{Arc, Mutex, MutexGuard};

use crate::workspace_crate::{
    noop_runtime_metrics_recorder, CgroupMonitorConfig, CgroupMonitorRegistry,
    RuntimeMetricsRecorderHandle, WorkspaceRuntimeService, WorkspaceSessionId,
};
use crate::workspace_session::WorkspaceSessionError;

use super::model::WorkspaceSession;

pub struct WorkspaceSessionService {
    sessions: Mutex<HashMap<WorkspaceSessionId, WorkspaceSession>>,
    workspace: Arc<WorkspaceRuntimeService>,
    cgroup_monitor: Arc<CgroupMonitorRegistry>,
    metrics: RuntimeMetricsRecorderHandle,
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
        Self::with_cgroup_monitor_and_metrics(
            workspace,
            cgroup_monitor,
            noop_runtime_metrics_recorder(),
        )
    }

    #[must_use]
    pub fn with_cgroup_monitor_and_metrics(
        workspace: Arc<WorkspaceRuntimeService>,
        cgroup_monitor: CgroupMonitorConfig,
        metrics: RuntimeMetricsRecorderHandle,
    ) -> Self {
        Self {
            sessions: Mutex::new(HashMap::new()),
            workspace,
            cgroup_monitor: Arc::new(CgroupMonitorRegistry::with_metrics_recorder(
                cgroup_monitor,
                Arc::clone(&metrics),
            )),
            metrics,
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

    #[must_use]
    pub(crate) fn metrics(&self) -> &RuntimeMetricsRecorderHandle {
        &self.metrics
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
