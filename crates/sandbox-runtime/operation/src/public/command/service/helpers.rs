use super::core::CommandOperationService;

use sandbox_runtime_command::process::CommandProcessExit;
use sandbox_runtime_command::yield_wait_loop::WaitOutcome;

use crate::command::{
    CommandOutputSnapshot, CommandServiceError, CommandSessionId, CommandStatus, CommandYield,
};
use crate::workspace_crate::WorkspaceSessionId;

impl CommandOperationService {
    pub(crate) fn running_command_yield(
        command_session_id: CommandSessionId,
        stdout: String,
    ) -> CommandYield {
        CommandYield {
            command_session_id: Some(command_session_id),
            status: CommandStatus::Running,
            exit_code: None,
            output: CommandOutputSnapshot { stdout },
            finalized: None,
        }
    }

    pub(crate) fn command_yield_from_wait_outcome(
        &self,
        command_session_id: CommandSessionId,
        outcome: WaitOutcome<CommandProcessExit>,
    ) -> Result<CommandYield, CommandServiceError> {
        match outcome {
            WaitOutcome::Running(stdout) => {
                Ok(Self::running_command_yield(command_session_id, stdout))
            }
            WaitOutcome::Completed(process_exit) => {
                self.completed_command_yield(command_session_id, process_exit)
            }
        }
    }

    pub(crate) fn completed_command_yield(
        &self,
        command_session_id: CommandSessionId,
        process_exit: CommandProcessExit,
    ) -> Result<CommandYield, CommandServiceError> {
        let result = self.complete_terminal_command(command_session_id.clone(), process_exit)?;
        let finalized = self
            .process_store()
            .completed(&command_session_id)
            .and_then(|completed| completed.finalized);
        Ok(CommandYield {
            command_session_id: Some(command_session_id),
            status: result.status,
            exit_code: result.exit_code,
            output: CommandOutputSnapshot {
                stdout: result.stdout,
            },
            finalized,
        })
    }

    pub(crate) fn ensure_workspace_session_not_remount_pending(
        &self,
        workspace_session_id: &WorkspaceSessionId,
    ) -> Result<(), CommandServiceError> {
        if self.workspace().is_remount_pending(workspace_session_id) {
            return Err(CommandServiceError::WorkspaceSessionRemountPending {
                workspace_session_id: workspace_session_id.clone(),
            });
        }
        if self.workspace().is_remount_blocked(workspace_session_id) {
            return Err(CommandServiceError::WorkspaceSessionRemountBlocked {
                workspace_session_id: workspace_session_id.clone(),
            });
        }
        Ok(())
    }
}
