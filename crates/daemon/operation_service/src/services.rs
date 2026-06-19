use std::sync::Arc;

use crate::command::CommandOperationService;
use crate::workspace_remount::WorkspaceRemountService;
use crate::workspace_session::WorkspaceSessionService;

#[derive(Clone)]
pub struct OperationServices {
    pub workspace: Arc<WorkspaceSessionService>,
    pub command: Arc<CommandOperationService>,
    pub remount: Arc<WorkspaceRemountService>,
}

impl OperationServices {
    #[must_use]
    pub fn new(
        workspace: Arc<WorkspaceSessionService>,
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
