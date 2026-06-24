use std::path::PathBuf;

use sandbox_runtime_namespace_process::runner::protocol::NsFds;

#[derive(Debug, Clone)]
pub struct NamespaceTarget {
    pub workspace_root: PathBuf,
    pub layer_paths: Vec<PathBuf>,
    pub upperdir: Option<PathBuf>,
    pub workdir: Option<PathBuf>,
    pub ns_fds: NsFds,
}
