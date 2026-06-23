use std::path::PathBuf;

use crate::command::CommandSessionId;
use crate::workspace_crate::{WorkspaceProfile, WorkspaceSessionId};

#[derive(Debug, Clone, Default, PartialEq)]
pub struct RuntimeObservabilitySnapshot {
    pub workspaces: Vec<RuntimeWorkspaceSnapshot>,
    pub active_executions: Vec<RuntimeExecutionSnapshot>,
    pub partial_errors: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeWorkspaceSnapshot {
    pub workspace_id: WorkspaceSessionId,
    pub remount_state: String,
    pub profile: WorkspaceProfile,
    pub workspace_root: PathBuf,
    pub upperdir: Option<PathBuf>,
    pub workdir: Option<PathBuf>,
    pub namespace_fd_count: Option<usize>,
    pub base_manifest_version: Option<i64>,
    pub base_root_hash: Option<String>,
    pub layer_count: Option<usize>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RuntimeExecutionSnapshot {
    pub execution_id: String,
    pub execution_kind: String,
    pub operation: Option<String>,
    pub command_session_id: Option<CommandSessionId>,
    pub workspace_id: WorkspaceSessionId,
    pub command: Option<String>,
    pub lifecycle_state: String,
    pub finalization_state: String,
    pub workspace_ownership: String,
    pub started_at_unix_ms: Option<i64>,
    pub wall_time_ms: Option<f64>,
    pub transcript_path: Option<PathBuf>,
    pub process_group_id: Option<i32>,
}
