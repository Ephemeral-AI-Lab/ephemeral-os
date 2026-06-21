//! Daemon↔namespace-runner protocol DTOs.

use std::os::unix::io::RawFd;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[repr(transparent)]
pub struct Fd(pub RawFd);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceRoot(pub PathBuf);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct NsFds {
    pub user: Option<Fd>,
    pub mnt: Option<Fd>,
    pub pid: Option<Fd>,
    pub net: Option<Fd>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NamespaceCommandRequest {
    pub request_id: String,
    pub args: Value,
    pub workspace_root: WorkspaceRoot,
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
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RunResult {
    pub exit_code: i32,
    pub payload: Value,
}
