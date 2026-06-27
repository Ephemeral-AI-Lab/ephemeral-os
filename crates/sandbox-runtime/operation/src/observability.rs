use std::path::PathBuf;

use crate::namespace_execution::RuntimeNamespaceExecutionSnapshot;
use crate::workspace_crate::{NetworkProfile, WorkspaceSessionId};

#[derive(Debug, Clone, Default, PartialEq)]
pub struct RuntimeObservabilitySnapshot {
    pub workspaces: Vec<RuntimeWorkspaceSnapshot>,
    pub active_namespace_executions: Vec<RuntimeNamespaceExecutionSnapshot>,
    pub partial_errors: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeWorkspaceSnapshot {
    pub workspace_id: WorkspaceSessionId,
    pub profile: NetworkProfile,
    pub workspace_root: PathBuf,
    pub upperdir: Option<PathBuf>,
    pub workdir: Option<PathBuf>,
    pub namespace_fd_count: Option<usize>,
    pub base_manifest_version: Option<i64>,
    pub base_root_hash: Option<String>,
    pub layer_count: Option<usize>,
    pub cgroup_path: Option<PathBuf>,
}
