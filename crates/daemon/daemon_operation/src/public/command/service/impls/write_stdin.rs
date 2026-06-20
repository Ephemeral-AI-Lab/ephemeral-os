use std::sync::Arc;

use crate::command::service::CommandOperationService;
use crate::command::{CommandCallContext, CommandServiceError, CommandYield, WriteStdinInput};

impl CommandOperationService {
    pub fn write_stdin(
        &self,
        input: WriteStdinInput,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError> {
        let command_id = input.command_id;
        let yield_time_ms = input
            .yield_time_ms
            .unwrap_or(self.config().default_yield_time_ms);
        let (process, workspace_session_id) = {
            let active = self.active_for_owner(&command_id, &context.caller_id)?;
            (
                Arc::clone(&active.process),
                active.workspace_session_id.clone(),
            )
        };
        self.ensure_workspace_session_not_remount_pending(&workspace_session_id)?;
        let output = {
            process.write_process_stdin(&input.chars).map_err(|error| {
                CommandServiceError::CommandIo {
                    command_id: command_id.clone(),
                    error: error.to_string(),
                }
            })?;
            if yield_time_ms == 0 {
                String::new()
            } else {
                process.read_output_since(0)
            }
        };

        Ok(Self::running_command_yield(command_id, output))
    }
}
