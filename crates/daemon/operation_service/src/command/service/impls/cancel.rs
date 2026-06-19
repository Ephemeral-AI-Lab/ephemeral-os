use std::time::Instant;

use crate::command::service::CommandOperationService;
use crate::command::{
    CancelCommandInput, CancellationState, CommandCallContext, CommandLifecycleState,
    CommandOutputSnapshot, CommandServiceError, CommandStatus, CommandYield,
};

impl CommandOperationService {
    pub fn cancel(
        &self,
        input: CancelCommandInput,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError> {
        let command_id = input.command_id;
        self.ensure_active_owner(&command_id, &context.caller_id)?;
        let output = self
            .process_store()
            .update_active(&command_id, |active| {
                if let Some(token) = active.remount_cancellation.clone() {
                    token.request_cancel();
                } else {
                    active.process.cancel_process();
                    active.lifecycle_state = CommandLifecycleState::Cancelled;
                }
                active.cancellation = CancellationState::Requested {
                    requested_at: Instant::now(),
                };
                active.process.read_output_since(0)
            })
            .ok_or_else(|| CommandServiceError::CommandNotFound {
                command_id: command_id.clone(),
            })?;

        Ok(CommandYield {
            command_id: Some(command_id),
            status: CommandStatus::Running,
            exit_code: None,
            output: CommandOutputSnapshot { stdout: output },
            finalized: None,
        })
    }
}
