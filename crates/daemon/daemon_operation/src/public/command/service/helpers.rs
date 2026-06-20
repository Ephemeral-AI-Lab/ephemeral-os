use super::core::CommandOperationService;

use crate::command::{
    CommandId, CommandOutputSnapshot, CommandServiceError, CommandStatus, CommandYield,
};
use crate::workspace_crate::WorkspaceId;

impl CommandOperationService {
    pub(crate) fn running_command_yield(command_id: CommandId, stdout: String) -> CommandYield {
        CommandYield {
            command_id: Some(command_id),
            status: CommandStatus::Running,
            exit_code: None,
            output: CommandOutputSnapshot { stdout },
            finalized: None,
        }
    }

    pub(crate) fn ensure_workspace_session_not_remount_pending(
        &self,
        workspace_session_id: &WorkspaceId,
    ) -> Result<(), CommandServiceError> {
        if self.workspace().is_remount_pending(workspace_session_id) {
            return Err(CommandServiceError::WorkspaceSessionRemountPending {
                workspace_session_id: workspace_session_id.clone(),
            });
        }
        Ok(())
    }
}
