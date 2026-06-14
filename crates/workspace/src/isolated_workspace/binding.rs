use std::collections::HashMap;
use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct IsolatedWorkspaceBinding {
    pub caller_id: String,
    pub workspace_handle_id: String,
    pub layer_stack_root: PathBuf,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
    pub workspace_root: PathBuf,
    pub scratch_dir: PathBuf,
    pub upperdir: PathBuf,
    pub workdir: PathBuf,
    pub layer_paths: Vec<PathBuf>,
    pub ns_fds: HashMap<String, i32>,
    pub cgroup_path: Option<PathBuf>,
}
