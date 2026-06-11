use std::collections::HashMap;
use std::path::PathBuf;

use crate::network::VethAllocation;

use super::IsolatedWorkspaceId;

#[derive(Debug, Clone)]
pub struct WorkspaceHandle {
    pub workspace_id: IsolatedWorkspaceId,
    pub caller_id: String,
    pub lease_id: String,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
    pub workspace_root: String,
    pub scratch_dir: PathBuf,
    pub upperdir: PathBuf,
    pub workdir: PathBuf,
    pub layer_paths: Vec<PathBuf>,
    pub ns_fds: HashMap<String, i32>,
    pub holder_pid: i32,
    pub readiness_fd: i32,
    pub control_fd: i32,
    pub veth: Option<VethAllocation>,
    pub cgroup_path: Option<PathBuf>,
    pub created_at: f64,
    pub last_activity: f64,
}
