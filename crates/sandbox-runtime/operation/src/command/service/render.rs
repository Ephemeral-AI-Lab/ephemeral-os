use sandbox_runtime_namespace_execution::{
    CommandTranscriptRow, CommandTranscriptWindow, NamespaceExecutionId,
    NamespaceExecutionTerminalStatus,
};

use crate::command::{CommandOutput, CommandStatus};

/// Build the merged `CommandOutput` DTO from a transcript window plus the
/// status/exit/timing projection. `command_session_id` is `Some` for running
/// reads and terminal reads that still have output to drain. The
/// `workspace_session_id`, `publish_rejected`, and `finalization_failed`
/// fields start `None`; the yield/read paths populate them from the command's
/// `CommandExecValue`.
#[must_use]
pub(crate) fn command_output(
    window: CommandTranscriptWindow,
    command_session_id: Option<NamespaceExecutionId>,
    status: CommandStatus,
    exit_code: Option<i64>,
    wall_time_seconds: f64,
    command_total_time_seconds: f64,
) -> CommandOutput {
    let output = render_transcript_text(&window.output);
    CommandOutput {
        command_session_id,
        workspace_session_id: None,
        status,
        exit_code,
        wall_time_seconds,
        command_total_time_seconds,
        start_offset: window.offset,
        end_offset: window.next_offset,
        total_lines: window.total_lines,
        original_token_count: estimate_token_count(output.len()),
        output,
        publish_rejected: None,
        finalization_failed: None,
    }
}

#[must_use]
pub(crate) fn estimate_token_count(chars: usize) -> u64 {
    if chars == 0 {
        0
    } else {
        u64::try_from(chars.div_ceil(4)).unwrap_or(u64::MAX)
    }
}

fn render_transcript_text(rows: &[CommandTranscriptRow]) -> String {
    rows.iter()
        .map(|row| row.text.as_str())
        .collect::<Vec<_>>()
        .join("\n")
}

pub(crate) const fn command_status(status: NamespaceExecutionTerminalStatus) -> CommandStatus {
    match status {
        NamespaceExecutionTerminalStatus::Ok => CommandStatus::Ok,
        NamespaceExecutionTerminalStatus::Error => CommandStatus::Error,
        NamespaceExecutionTerminalStatus::TimedOut => CommandStatus::TimedOut,
        NamespaceExecutionTerminalStatus::Cancelled => CommandStatus::Cancelled,
    }
}
