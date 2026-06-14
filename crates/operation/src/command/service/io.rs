use std::time::Instant;

use command::{CancelCommand, CommandError, ReadCommandProgress, WriteStdin};

use crate::command::contract::CommandResponse;

use super::{
    contains_teardown_control, elapsed_ms, is_teardown_control, progress_trace, CommandOps,
    CommandReadProgressOutcome, CommandStdinTraceFacts, CommandWriteStdinOutcome,
};

impl CommandOps {
    pub fn write_stdin_with_trace(
        &self,
        request: WriteStdin,
    ) -> Result<CommandWriteStdinOutcome, CommandError> {
        if is_teardown_control(&request.chars) {
            let response = self.cancel(CancelCommand {
                command_id: request.command_id,
            })?;
            return Ok(CommandWriteStdinOutcome {
                response,
                trace: None,
            });
        }
        if contains_teardown_control(&request.chars) {
            return Err(CommandError::InvalidRequest(
                "Ctrl-C/Ctrl-D must be sent alone to cancel command process".to_owned(),
            ));
        }
        let Some(run) = self.registry.get(&request.command_id) else {
            return Err(CommandError::NotFound(request.command_id));
        };
        if request.chars.is_empty() {
            return Err(CommandError::InvalidRequest(
                "chars must be non-empty".to_owned(),
            ));
        }
        let bytes = request.chars.len();
        let waited_for_output = request.yield_time_ms > 0;
        let command_id = request.command_id.clone();
        let start_offset = run.process().transcript_len();
        let wait_started = Instant::now();
        run.process().write_process_stdin(&request.chars)?;
        let response = self.wait_on_run(run, request.yield_time_ms, start_offset, true, |stdout| {
            CommandResponse::running(command_id.clone(), stdout)
        });
        let status = response.status;
        Ok(CommandWriteStdinOutcome {
            response,
            trace: Some(CommandStdinTraceFacts {
                command_id,
                bytes,
                wait_ms: elapsed_ms(wait_started),
                waited_for_output,
                status,
            }),
        })
    }

    pub fn read_command_progress(
        &self,
        request: ReadCommandProgress,
    ) -> Result<CommandResponse, CommandError> {
        self.read_command_progress_with_trace(request)
            .map(|outcome| outcome.response)
    }

    pub fn read_command_progress_with_trace(
        &self,
        request: ReadCommandProgress,
    ) -> Result<CommandReadProgressOutcome, CommandError> {
        if request.last_n_lines == 0 {
            return Err(CommandError::InvalidRequest(
                "last_n_lines must be >= 1".to_owned(),
            ));
        }
        let Some(run) = self.registry.get(&request.command_id) else {
            let response = self
                .registry
                .completed_result(&request.command_id)
                .map(|result| result.with_last_lines(request.last_n_lines))
                .ok_or_else(|| CommandError::NotFound(request.command_id.clone()))?;
            return Ok(CommandReadProgressOutcome {
                trace: progress_trace(
                    &request.command_id,
                    request.last_n_lines,
                    "completed_buffer",
                    &response,
                ),
                response,
            });
        };
        if let Some(process_exit) = run.process().take_exit() {
            let response = self
                .finalize_command(run, process_exit, false, true)
                .with_last_lines(request.last_n_lines);
            return Ok(CommandReadProgressOutcome {
                trace: progress_trace(
                    &request.command_id,
                    request.last_n_lines,
                    "finalized",
                    &response,
                ),
                response,
            });
        }
        let response = CommandResponse::running(
            request.command_id.clone(),
            run.process().read_recent_output(request.last_n_lines),
        );
        Ok(CommandReadProgressOutcome {
            trace: progress_trace(&request.command_id, request.last_n_lines, "live", &response),
            response,
        })
    }

    pub fn cancel(&self, request: CancelCommand) -> Result<CommandResponse, CommandError> {
        let Some(run) = self.registry.get(&request.command_id) else {
            return self
                .registry
                .take_completed_result(&request.command_id)
                .ok_or(CommandError::NotFound(request.command_id));
        };
        let start_offset = run.process().transcript_len();
        run.process().cancel_process();
        Ok(self.wait_on_run(
            run,
            self.config.cancel_wait_ms,
            start_offset,
            true,
            CommandResponse::cancelled,
        ))
    }
}
