use std::sync::Arc;

use crate::command::CommandOperationService;
use crate::workspace_manager::WorkspaceManagerService;
use crate::workspace_remount::WorkspaceRemountService;

#[derive(Clone)]
pub struct OperationServices {
    pub workspace: Arc<WorkspaceManagerService>,
    pub command: Arc<CommandOperationService>,
    pub remount: Arc<WorkspaceRemountService>,
}

impl OperationServices {
    #[must_use]
    pub fn new(
        workspace: Arc<WorkspaceManagerService>,
        command: Arc<CommandOperationService>,
        remount: Arc<WorkspaceRemountService>,
    ) -> Self {
        Self {
            workspace,
            command,
            remount,
        }
    }
}
