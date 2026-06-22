//! Daemon↔namespace-runner protocol DTOs.

use std::os::unix::io::RawFd;
use std::path::PathBuf;
use std::sync::Arc;

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[repr(transparent)]
pub struct Fd(pub RawFd);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct NsFds {
    pub user: Option<Fd>,
    pub mnt: Option<Fd>,
    pub pid: Option<Fd>,
    pub net: Option<Fd>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NamespaceRunnerRequest {
    pub request_id: String,
    pub args: Value,
    pub workspace_root: PathBuf,
    pub layer_paths: Vec<PathBuf>,
    #[serde(default)]
    pub upperdir: Option<PathBuf>,
    #[serde(default)]
    pub workdir: Option<PathBuf>,
    #[serde(default)]
    pub ns_fds: Option<NsFds>,
    #[serde(default)]
    pub cgroup_path: Option<PathBuf>,
    #[serde(default)]
    pub timeout_seconds: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trace_context: Option<TraceContext>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TraceContext {
    pub traceparent: String,
    #[serde(default)]
    pub tracestate: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RunResult {
    pub exit_code: i32,
    pub payload: Value,
}

pub type CurrentTraceContext = Arc<dyn Fn() -> Option<TraceContext> + Send + Sync + 'static>;

pub fn no_trace_context() -> CurrentTraceContext {
    Arc::new(|| None)
}
