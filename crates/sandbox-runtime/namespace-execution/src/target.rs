use std::path::PathBuf;

use sandbox_runtime_namespace_process::runner::protocol::NsFds;

/// Workspace identity for a namespace execution; built once, reused per exec.
/// No timeout: that is per-exec and lives on the operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NamespaceTarget {
    pub workspace_root: PathBuf,
    pub layer_paths: Vec<PathBuf>,
    pub upperdir: Option<PathBuf>,
    pub workdir: Option<PathBuf>,
    pub ns_fds: NsFds,
}
