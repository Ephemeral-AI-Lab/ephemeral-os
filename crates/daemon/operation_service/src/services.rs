use std::sync::Arc;

use crate::command::{
    CommandCallContext, CommandOperationService, CommandServiceError, CommandYield,
    ExecCommandInput, OperationTraceContext,
};
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

    pub fn exec_command(
        &self,
        input: ExecCommandInput,
        trace: OperationTraceContext,
    ) -> Result<CommandYield, CommandServiceError> {
        let caller_id = input.caller_id.clone();
        let workspace = match input.workspace_id.clone() {
            Some(workspace_id) => Some(self.workspace.resolve(workspace_id, caller_id.clone())?),
            None => None,
        };

        if let Some(handler) = &workspace {
            if handler.handle.workspace_root != input.workspace_root {
                return Err(CommandServiceError::WorkspaceRootMismatch {
                    expected: handler.handle.workspace_root.clone(),
                    actual: input.workspace_root.clone(),
                });
            }
        }

        self.command
            .exec_command(input, workspace, CommandCallContext { caller_id, trace })
    }
}
