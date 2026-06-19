use crate::command::service::CommandOperationService;
use crate::command::{
    CommandCallContext, CommandOutputSnapshot, CommandPollOutput, CommandServiceError,
    CommandStatus, PollCommandInput,
};

impl CommandOperationService {
    pub fn poll(
        &self,
        input: PollCommandInput,
        context: CommandCallContext,
    ) -> Result<CommandPollOutput, CommandServiceError> {
        let command_id = input.command_id;
        if let Some(active) = self.active_for_owner_or_none(&command_id, &context.caller_id)? {
            if active.process.process_group_id().is_some() {
                if let Some(process_exit) = active.process.take_exit() {
                    drop(active);
                    let result = self.finalize_command(command_id.clone(), process_exit)?;
                    let completed = self.completed_for_owner(&command_id, &context.caller_id)?;
                    let stdout = input.last_n_lines.map_or_else(
                        || result.stdout.clone(),
                        |last_n_lines| ::command::tail_lines(&result.stdout, last_n_lines),
                    );
                    return Ok(CommandPollOutput {
                        command_id,
                        status: result.status,
                        exit_code: result.exit_code,
                        output: CommandOutputSnapshot { stdout },
                        finalized: completed.finalized,
                    });
                }
            }
            let stdout = active
                .process
                .read_recent_output(input.last_n_lines.unwrap_or(200));
            return Ok(CommandPollOutput {
                command_id,
                status: CommandStatus::Running,
                exit_code: None,
                output: CommandOutputSnapshot { stdout },
                finalized: None,
            });
        }

        let completed = self.completed_for_owner(&command_id, &context.caller_id)?;
        let stdout = input.last_n_lines.map_or_else(
            || completed.result.stdout.clone(),
            |last_n_lines| ::command::tail_lines(&completed.result.stdout, last_n_lines),
        );
        Ok(CommandPollOutput {
            command_id,
            status: completed.result.status,
            exit_code: completed.result.exit_code,
            output: CommandOutputSnapshot { stdout },
            finalized: completed.finalized,
        })
    }
}
