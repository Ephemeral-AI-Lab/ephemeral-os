use std::sync::Arc;

use crate::command::{CommandRemountInspection, CommandRemountQuiesce};
use crate::workspace_crate::WorkspaceId;
use crate::workspace_session::remount::RemountWorkspaceSession;
use crate::workspace_session::WorkspaceSessionHandler;

pub trait CommandRemountCoordinator: Send + Sync {
    fn begin_workspace_remount_quiesce(
        &self,
        workspace_session_id: &WorkspaceId,
    ) -> CommandRemountQuiesce;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceRemountReport {
    pub workspace_session_id: WorkspaceId,
    pub remounted: bool,
    pub blocked_reason: Option<String>,
    pub command_inspection: CommandRemountInspection,
    pub updated_handler: Option<WorkspaceSessionHandler>,
}

pub struct WorkspaceRemountService {
    pub(super) workspace: Arc<dyn RemountWorkspaceSession>,
    pub(super) command: Arc<dyn CommandRemountCoordinator>,
}

impl WorkspaceRemountService {
    #[must_use]
    pub fn new(
        workspace: Arc<dyn RemountWorkspaceSession>,
        command: Arc<dyn CommandRemountCoordinator>,
    ) -> Self {
        Self { workspace, command }
    }
}
