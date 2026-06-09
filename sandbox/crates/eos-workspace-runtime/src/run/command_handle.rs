use std::collections::HashMap;
use std::path::PathBuf;

/// Per-command-session snapshot of a caller's isolated workspace state (pure
/// data: namespace fds, scratch dirs, lease/manifest coordinates). The isolated
/// workspace run owns one of these per session; it carries everything the run
/// needs to build the set-ns runner request and finalize for audit.
///
/// Constructed by the daemon from its isolated-session state and stored in the
/// run [`crate::registry`]; the namespace + lease themselves are owned by the
/// daemon's isolated-session subsystem and torn down on `exit`.
#[derive(Debug, Clone)]
pub struct CommandHandle {
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
