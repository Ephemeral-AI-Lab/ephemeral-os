use crate::command::service::CommandOperationService;
use crate::command::{
    CommandCallContext, CommandLinesOutput, CommandServiceError, CommandStatus,
    ReadCommandLinesInput,
};

impl CommandOperationService {
    pub fn read_lines(
        &self,
        input: ReadCommandLinesInput,
        context: CommandCallContext,
    ) -> Result<CommandLinesOutput, CommandServiceError> {
        let command_id = input.command_id;
        if let Some(active) = self.active_for_owner_or_none(&command_id, &context.caller_id)? {
            let transcript = active.transcript.clone();
            drop(active);
            return Ok(transcript.window(input.offset, input.limit).into_output(
                command_id,
                CommandStatus::Running,
                None,
            ));
        }

        let completed = self.completed_for_owner(&command_id, &context.caller_id)?;
        Ok(completed
            .transcript
            .window(&command_id, input.offset, input.limit)?
            .into_output(
                command_id,
                completed.result.status,
                completed.result.exit_code,
            ))
    }
}
