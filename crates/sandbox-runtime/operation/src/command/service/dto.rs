use sandbox_runtime_namespace_execution::NamespaceExecutionId;

use crate::workspace_crate::WorkspaceSessionId;

#[derive(Debug, Clone, PartialEq)]
pub struct ExecCommandInput {
    pub workspace_session_id: Option<WorkspaceSessionId>,
    pub cmd: String,
    pub timeout_ms: Option<u64>,
    pub yield_time_ms: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WriteCommandStdinInput {
    pub command_session_id: NamespaceExecutionId,
    pub stdin: String,
    pub yield_time_ms: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadCommandLinesInput {
    pub command_session_id: NamespaceExecutionId,
    pub start_offset: Option<u64>,
    pub limit: Option<usize>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommandStatus {
    Running,
    Ok,
    Error,
    TimedOut,
    Cancelled,
}

impl CommandStatus {
    #[must_use]
    pub(crate) const fn as_str(self) -> &'static str {
        match self {
            Self::Running => "running",
            Self::Ok => "ok",
            Self::Error => "error",
            Self::TimedOut => "timed_out",
            Self::Cancelled => "cancelled",
        }
    }
}

/// The single command output DTO: the merge of the former `CommandYield`,
/// `CommandLinesOutput`, and `CommandOutputSnapshot`. `command_session_id` is
/// `Option` (the superset): yields include it only when the command is still
/// running or has more output to drain; `read_command_lines` always sets it.
/// `workspace_session_id` is an identifier, not a liveness promise — the
/// session may already be finalized when the caller reads it (§2.6).
/// `publish_rejected` carries the reject class when this command's completion
/// ran a finalize whose publish was rejected; terminal responses only.
#[derive(Debug, Clone, PartialEq)]
pub struct CommandOutput {
    pub command_session_id: Option<NamespaceExecutionId>,
    pub workspace_session_id: Option<WorkspaceSessionId>,
    pub status: CommandStatus,
    pub exit_code: Option<i64>,
    pub wall_time_seconds: f64,
    pub command_total_time_seconds: f64,
    pub start_offset: u64,
    pub end_offset: u64,
    pub total_lines: u64,
    pub original_token_count: u64,
    pub output: String,
    pub publish_rejected: Option<&'static str>,
}
