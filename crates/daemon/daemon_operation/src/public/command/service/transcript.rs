use crate::command::{CommandLinesOutput, CommandServiceError, CommandSessionId, CommandStatus};

use super::process_store::{CommandTranscriptStore, RetainedCommandTranscript};

impl CommandTranscriptStore {
    #[must_use]
    pub(crate) fn window(&self, offset: u64, limit: usize) -> ::command::CommandTranscriptWindow {
        ::command::transcript_window(self.transcript_path.as_deref(), offset, limit)
    }
}

impl RetainedCommandTranscript {
    pub(crate) fn window(
        &self,
        command_session_id: &CommandSessionId,
        offset: u64,
        limit: usize,
    ) -> Result<::command::CommandTranscriptWindow, CommandServiceError> {
        ::command::required_transcript_window(self.transcript_path.as_deref(), offset, limit)
            .map_err(|error| CommandServiceError::CommandTranscriptUnavailable {
                command_session_id: command_session_id.clone(),
                path: self.transcript_path.clone(),
                error,
            })
    }
}

pub(crate) trait CommandTranscriptWindowExt {
    fn into_output(
        self,
        command_session_id: CommandSessionId,
        status: CommandStatus,
        exit_code: Option<i64>,
    ) -> CommandLinesOutput;
}

impl CommandTranscriptWindowExt for ::command::CommandTranscriptWindow {
    fn into_output(
        self,
        command_session_id: CommandSessionId,
        status: CommandStatus,
        exit_code: Option<i64>,
    ) -> CommandLinesOutput {
        CommandLinesOutput {
            command_session_id,
            status,
            exit_code,
            start_offset: self.offset,
            end_offset: self.next_offset,
            total_lines: self.total_lines,
            truncated_before: self.truncated_before,
            output_truncated: self.output_truncated,
            output: self.output,
        }
    }
}
