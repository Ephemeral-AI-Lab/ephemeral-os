use std::collections::BTreeMap;
use std::path::PathBuf;

use crate::workspace_crate::{CallerId, ChangedPathKind, WorkspaceId};

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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandFinalizedMetadata {
    pub policy: CommandFinalizedPolicy,
    pub outcome: CommandFinalizationOutcome,
    pub changed_paths: Vec<String>,
    pub changed_path_kinds: BTreeMap<String, ChangedPathKind>,
    pub protected_drop_count: usize,
    pub captured_change_count: usize,
    pub route_stats: layerstack::CaptureRouteStats,
    pub metadata_path_count: usize,
    pub spool_dir_cleaned: bool,
    pub published_manifest_version: Option<u64>,
    pub destroy: Option<CommandWorkspaceDestroyMetadata>,
}

impl Default for CommandFinalizedMetadata {
    fn default() -> Self {
        Self {
            policy: CommandFinalizedPolicy::Session,
            outcome: CommandFinalizationOutcome::SessionComplete,
            changed_paths: Vec::new(),
            changed_path_kinds: BTreeMap::new(),
            protected_drop_count: 0,
            captured_change_count: 0,
            route_stats: layerstack::CaptureRouteStats::default(),
            metadata_path_count: 0,
            spool_dir_cleaned: false,
            published_manifest_version: None,
            destroy: None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommandFinalizedPolicy {
    Session,
    OneShotPublishThenDestroy,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommandFinalizationOutcome {
    SessionComplete,
    Published,
    Discarded,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandWorkspaceDestroyMetadata {
    pub evicted_upperdir_bytes: u64,
    pub lease_released: Option<bool>,
    pub lease_release_error: Option<String>,
    pub active_leases_after: usize,
}

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
