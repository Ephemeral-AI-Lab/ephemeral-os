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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommandStatus {
    Running,
    Completed,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandYield {
    pub command_id: Option<CommandId>,
    pub status: CommandStatus,
    pub exit_code: Option<i64>,
}
