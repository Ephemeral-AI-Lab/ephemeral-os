#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ObservabilitySnapshotReadOptions {
    pub include_recent_traces: bool,
    pub trace_limit: usize,
    pub resource_window_ms: Option<u64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ObservabilitySnapshotRows {
    pub sandbox: Option<ObservabilitySandboxSnapshotRow>,
    pub workspaces: Vec<ObservabilityWorkspaceSnapshotRow>,
    pub active_namespace_executions: Vec<ObservabilityNamespaceExecutionSnapshotRow>,
    pub latest_resources: Vec<ObservabilityResourceSampleRow>,
    pub resource_history: Vec<ObservabilityResourceSampleRow>,
    pub recent_request_traces: Vec<ObservabilityRequestTraceRow>,
    pub recent_namespace_traces: Vec<ObservabilityNamespaceExecutionTraceRow>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ObservabilitySandboxSnapshotRow {
    pub sandbox_id: String,
    pub state: String,
    pub daemon_runtime_dir: Option<String>,
    pub socket_path: Option<String>,
    pub pid_path: Option<String>,
    pub daemon_pid: Option<i64>,
    pub sampled_at_unix_ms: i64,
    pub error_message: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ObservabilityWorkspaceSnapshotRow {
    pub workspace_id: String,
    pub state: String,
    pub profile: Option<String>,
    pub namespace_fd_count: Option<i64>,
    pub base_manifest_version: Option<i64>,
    pub base_root_hash: Option<String>,
    pub layer_count: Option<i64>,
    pub sampled_at_unix_ms: i64,
    pub error_message: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ObservabilityNamespaceExecutionSnapshotRow {
    pub namespace_execution_id: String,
    pub workspace_session_id: String,
    pub operation: String,
    pub lifecycle_state: String,
    pub sampled_at_unix_ms: i64,
    pub error_message: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ObservabilityResourceSampleRow {
    pub workspace_id: Option<String>,
    pub sampled_at_unix_ms: i64,
    pub cgroup_available: bool,
    pub cgroup_error: Option<String>,
    pub cpu_usage_usec: Option<i64>,
    pub memory_current_bytes: Option<i64>,
    pub memory_max_bytes: Option<i64>,
    pub memory_max_unlimited: Option<bool>,
    pub disk_upperdir_bytes: Option<i64>,
    pub disk_file_count: Option<i64>,
    pub disk_dir_count: Option<i64>,
    pub disk_symlink_count: Option<i64>,
    pub disk_truncated: Option<bool>,
    pub disk_read_error_count: Option<i64>,
    pub disk_first_error_path: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ObservabilityRequestTraceRow {
    pub trace_id: String,
    pub kind: String,
    pub status: String,
    pub operation: String,
    pub request_id: Option<String>,
    pub workspace_id: Option<String>,
    pub started_at_unix_ms: i64,
    pub finished_at_unix_ms: Option<i64>,
    pub duration_ms: Option<f64>,
    pub error_kind: Option<String>,
    pub error_message: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ObservabilityNamespaceExecutionTraceRow {
    pub trace_id: String,
    pub namespace_execution_id: String,
    pub workspace_session_id: String,
    pub operation: String,
    pub request_id: Option<String>,
    pub status: String,
    pub exit_code: Option<i64>,
    pub started_at_unix_ms: i64,
    pub finished_at_unix_ms: i64,
    pub duration_ms: f64,
    pub error_kind: Option<String>,
    pub error_message: Option<String>,
}
