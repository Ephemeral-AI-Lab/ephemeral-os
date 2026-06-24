use std::time::{Duration, Instant};

use sandbox_runtime_namespace_execution::{
    NamespaceExecutionError, NamespaceExecutionTerminalStatus,
};

use super::core::{execution_id, CommandOperationService};
use super::transcript::command_output;
use crate::command::CommandExecution;
use crate::command::{CommandOutput, CommandServiceError, CommandSessionId, CommandStatus};

const QUIET_MS: Duration = Duration::from_millis(50);

impl CommandOperationService {
    pub(crate) fn wait_for_command_yield(
        &self,
        command_session_id: CommandSessionId,
        yield_time_ms: u64,
        start_offset: u64,
        include_terminal_command_session_id: bool,
    ) -> Result<CommandOutput, CommandServiceError> {
        let id = execution_id(&command_session_id);
        let deadline = Instant::now() + Duration::from_millis(yield_time_ms);
        let mut last_offset = start_offset;
        let mut last_change = Instant::now();
        loop {
            let Some((finished, offset, waiter)) = self.engine().with_value(&id, |command| {
                (
                    command.is_finished(),
                    command.output_len(),
                    command.completion(),
                )
            }) else {
                return command_not_found(command_session_id);
            };
            if finished {
                return self.completed_command_output(
                    command_session_id,
                    include_terminal_command_session_id,
                );
            }
            let now = Instant::now();
            if offset != last_offset {
                last_offset = offset;
                last_change = now;
            }
            let settled = offset > start_offset && now.duration_since(last_change) >= QUIET_MS;
            if settled || now >= deadline {
                return match self.engine().with_value(&id, CommandExecution::is_finished) {
                    Some(true) => self.completed_command_output(
                        command_session_id,
                        include_terminal_command_session_id,
                    ),
                    Some(false) => self.running_command_output(command_session_id),
                    None => command_not_found(command_session_id),
                };
            }
            let slice = QUIET_MS.min(deadline.saturating_duration_since(now));
            waiter.wait_timeout(slice);
        }
    }

    fn running_command_output(
        &self,
        command_session_id: CommandSessionId,
    ) -> Result<CommandOutput, CommandServiceError> {
        let id = execution_id(&command_session_id);
        let output = self.engine().with_value(&id, |command| {
            let start = command.take_snapshot_offset();
            let window = command.transcript_window(start, usize::MAX);
            command.advance_snapshot_offset(window.next_offset);
            let elapsed = command.elapsed_seconds();
            command_output(window, None, CommandStatus::Running, None, elapsed, elapsed)
        });
        match output {
            Some(mut output) => {
                output.command_session_id = Some(command_session_id);
                Ok(output)
            }
            None => command_not_found(command_session_id),
        }
    }

    pub(crate) fn completed_command_output(
        &self,
        command_session_id: CommandSessionId,
        include_terminal_command_session_id: bool,
    ) -> Result<CommandOutput, CommandServiceError> {
        let id = execution_id(&command_session_id);
        let read = self.engine().with_value(&id, |command| {
            let result = command.terminal_result();
            let start = command.take_snapshot_offset();
            let window = command.transcript_window(start, usize::MAX);
            let elapsed = command.elapsed_seconds();
            (result, window, elapsed)
        });
        let Some((result, window, elapsed)) = read else {
            return command_not_found(command_session_id);
        };
        let result = match result {
            Some(Ok(result)) => result,
            Some(Err(error)) => {
                return Err(finalization_failed(command_session_id, &error));
            }
            None => return command_not_found(command_session_id),
        };
        let has_more_output = window.output_truncated;
        let returned_command_session_id =
            (include_terminal_command_session_id || has_more_output).then_some(command_session_id);
        Ok(command_output(
            window,
            returned_command_session_id,
            command_status(result.status),
            Some(result.exit_code),
            elapsed,
            result.command_total_time_seconds,
        ))
    }
}

pub(crate) const fn command_status(status: NamespaceExecutionTerminalStatus) -> CommandStatus {
    match status {
        NamespaceExecutionTerminalStatus::Ok => CommandStatus::Ok,
        NamespaceExecutionTerminalStatus::Error => CommandStatus::Error,
        NamespaceExecutionTerminalStatus::TimedOut => CommandStatus::TimedOut,
        NamespaceExecutionTerminalStatus::Cancelled => CommandStatus::Cancelled,
    }
}

pub(crate) fn finalization_failed(
    command_session_id: CommandSessionId,
    error: &NamespaceExecutionError,
) -> CommandServiceError {
    CommandServiceError::CommandFinalizationFailed {
        command_session_id,
        error: finalize_message(error),
    }
}

pub(crate) fn command_not_found<T>(
    command_session_id: CommandSessionId,
) -> Result<T, CommandServiceError> {
    Err(CommandServiceError::CommandNotFound { command_session_id })
}

pub(crate) fn finalize_message(error: &NamespaceExecutionError) -> String {
    match error {
        NamespaceExecutionError::Finalize(message) => message.clone(),
        other => other.to_string(),
    }
}
