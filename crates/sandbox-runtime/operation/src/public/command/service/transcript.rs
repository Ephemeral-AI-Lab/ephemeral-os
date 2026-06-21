use crate::command::{CommandLinesOutput, CommandServiceError, CommandSessionId, CommandStatus};

use super::process_store::{CommandTranscriptStore, RetainedCommandTranscript};

impl CommandTranscriptStore {
    #[must_use]
    pub(crate) fn window(
        &self,
        offset: u64,
        limit: usize,
    ) -> ::sandbox_runtime_command::CommandTranscriptWindow {
        ::sandbox_runtime_command::transcript_window(self.transcript_path.as_deref(), offset, limit)
    }
}

impl RetainedCommandTranscript {
    pub(crate) fn window(
        &self,
        command_session_id: &CommandSessionId,
        offset: u64,
        limit: usize,
    ) -> Result<::sandbox_runtime_command::CommandTranscriptWindow, CommandServiceError> {
        ::sandbox_runtime_command::required_transcript_window(
            self.transcript_path.as_deref(),
            offset,
            limit,
        )
        .map_err(|error| CommandServiceError::CommandTranscriptUnavailable {
            command_session_id: command_session_id.clone(),
            path: self.transcript_path.clone(),
            error,
        })
    }
}

#[must_use]
pub(crate) fn command_lines_output(
    window: ::sandbox_runtime_command::CommandTranscriptWindow,
    command_session_id: CommandSessionId,
    status: CommandStatus,
    exit_code: Option<i64>,
) -> CommandLinesOutput {
    CommandLinesOutput {
        command_session_id,
        status,
        exit_code,
        start_offset: window.offset,
        end_offset: window.next_offset,
        total_lines: window.total_lines,
        truncated_before: window.truncated_before,
        output_truncated: window.output_truncated,
        output: window.output,
    }
}
