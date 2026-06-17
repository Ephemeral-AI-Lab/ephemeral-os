use std::path::PathBuf;

use crate::workspace_crate::{CallerId, WorkspaceId};

#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct CommandId(pub String);

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct OperationTraceContext;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandCallContext {
    pub caller_id: CallerId,
    pub trace: OperationTraceContext,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ExecCommandInput {
    pub caller_id: CallerId,
    pub workspace_root: PathBuf,
    pub workspace_id: Option<WorkspaceId>,
    pub cmd: String,
    pub cwd: Option<PathBuf>,
    pub timeout_seconds: Option<f64>,
    pub yield_time_ms: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WriteStdinInput {
    pub command_id: CommandId,
    pub chars: String,
    pub yield_time_ms: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadCommandLinesInput {
    pub command_id: CommandId,
    pub offset: u64,
    pub limit: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PollCommandInput {
    pub command_id: CommandId,
    pub last_n_lines: Option<usize>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CancelCommandInput {
    pub command_id: CommandId,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommandStatus {
    Running,
    Completed,
    Failed,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CommandOutputSnapshot {
    pub stdout: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CommandFinalizedMetadata {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandYield {
    pub command_id: Option<CommandId>,
    pub status: CommandStatus,
    pub exit_code: Option<i64>,
    pub output: CommandOutputSnapshot,
    pub finalized: Option<CommandFinalizedMetadata>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandPollOutput {
    pub command_id: CommandId,
    pub status: CommandStatus,
    pub exit_code: Option<i64>,
    pub output: CommandOutputSnapshot,
    pub finalized: Option<CommandFinalizedMetadata>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandLinesOutput {
    pub command_id: CommandId,
    pub offset: u64,
    pub next_offset: u64,
    pub total_lines: u64,
    pub output_truncated: bool,
    pub output: Vec<CommandOutputLine>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandOutputLine {
    pub offset: u64,
    pub text: String,
}
