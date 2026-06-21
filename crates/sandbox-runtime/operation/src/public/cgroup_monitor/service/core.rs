use std::sync::Arc;

use crate::workspace_crate::CgroupMonitorRegistry;
use crate::workspace_session::WorkspaceSessionService;

pub struct CgroupMonitorOperationService {
    workspace: Arc<WorkspaceSessionService>,
    registry: Arc<CgroupMonitorRegistry>,
}

impl CgroupMonitorOperationService {
    #[must_use]
    pub fn new(workspace: Arc<WorkspaceSessionService>) -> Self {
        let registry = workspace.cgroup_monitor();
        Self {
            workspace,
            registry,
        }
    }

    #[must_use]
    pub(crate) fn workspace(&self) -> &Arc<WorkspaceSessionService> {
        &self.workspace
    }

    #[must_use]
    pub(crate) fn registry(&self) -> &Arc<CgroupMonitorRegistry> {
        &self.registry
    }
}
