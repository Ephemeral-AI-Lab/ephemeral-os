use std::sync::Arc;

use crate::workspace_crate::{
    noop_runtime_metrics_recorder, RuntimeMetricsRecorderHandle, WorkspaceSessionId,
};
use crate::workspace_session::WorkspaceSessionHandler;

use super::command::{CommandRemountInspection, CommandRemountQuiesce};
use super::workspace_session::RemountWorkspaceSession;

pub trait CommandRemountCoordinator: Send + Sync {
    fn begin_workspace_remount_quiesce(
        &self,
        workspace_session_id: &WorkspaceSessionId,
    ) -> CommandRemountQuiesce;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceRemountOutcome {
    pub workspace_session_id: WorkspaceSessionId,
    pub remounted: bool,
    pub blocked_reason: Option<String>,
    pub command_inspection: CommandRemountInspection,
    pub updated_handler: Option<WorkspaceSessionHandler>,
}

pub struct WorkspaceRemountService {
    pub(super) workspace: Arc<dyn RemountWorkspaceSession>,
    pub(super) command: Arc<dyn CommandRemountCoordinator>,
    pub(super) metrics: RuntimeMetricsRecorderHandle,
}

impl WorkspaceRemountService {
    #[must_use]
    pub fn new(
        workspace: Arc<dyn RemountWorkspaceSession>,
        command: Arc<dyn CommandRemountCoordinator>,
    ) -> Self {
        Self::with_metrics_recorder(workspace, command, noop_runtime_metrics_recorder())
    }

    #[must_use]
    pub fn with_metrics_recorder(
        workspace: Arc<dyn RemountWorkspaceSession>,
        command: Arc<dyn CommandRemountCoordinator>,
        metrics: RuntimeMetricsRecorderHandle,
    ) -> Self {
        Self {
            workspace,
            command,
            metrics,
        }
    }

    #[must_use]
    pub(crate) fn metrics(&self) -> &RuntimeMetricsRecorderHandle {
        &self.metrics
    }
}
