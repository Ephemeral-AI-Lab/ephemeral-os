//! Daemon-side cgroup v2 root discovery and self-vacation.
//!
//! The daemon owns only the discovery of its delegated cgroup root `R` and the
//! move of its own processes into `R/_daemon` so `R` can enable controllers for
//! the per-workspace child cgroups the runtime creates. Everything here is
//! best-effort: any failure yields `None` and cgroup accounting degrades to
//! `cgroup_available = false` without ever blocking the daemon.

use std::path::{Path, PathBuf};

const CGROUP_FS_ROOT: &str = "/sys/fs/cgroup";
const DAEMON_LEAF: &str = "_daemon";
const REQUIRED_CONTROLLERS: [&str; 2] = ["cpu", "memory"];

/// Discover the delegated cgroup v2 root `R`, vacate the daemon into
/// `R/_daemon`, and enable `+cpu +memory` for child cgroups. Returns `R` on
/// success, or `None` when cgroup v2 is unavailable or not delegated.
pub(crate) fn discover_and_prepare_root() -> Option<PathBuf> {
    let proc_self_cgroup = std::fs::read_to_string("/proc/self/cgroup").ok()?;
    let root = parse_cgroup_root(&proc_self_cgroup)?;
    prepare_root(&root).ok()?;
    Some(root)
}

/// Map the unified-hierarchy line `0::<path>` from `/proc/self/cgroup` onto the
/// filesystem cgroup root `/sys/fs/cgroup<path>`.
pub(crate) fn parse_cgroup_root(proc_self_cgroup: &str) -> Option<PathBuf> {
    let relative = proc_self_cgroup
        .lines()
        .find_map(|line| line.strip_prefix("0::"))?
        .trim();
    let relative = relative.strip_prefix('/').unwrap_or(relative);
    let mut root = PathBuf::from(CGROUP_FS_ROOT);
    if !relative.is_empty() {
        root.push(relative);
    }
    Some(root)
}

fn prepare_root(root: &Path) -> Result<(), String> {
    let daemon_leaf = root.join(DAEMON_LEAF);
    std::fs::create_dir_all(&daemon_leaf)
        .map_err(|error| format!("create {}: {error}", daemon_leaf.display()))?;
    std::fs::write(
        daemon_leaf.join("cgroup.procs"),
        std::process::id().to_string(),
    )
    .map_err(|error| format!("move daemon into {}: {error}", daemon_leaf.display()))?;
    enable_subtree_controllers(root);
    Ok(())
}

/// Enable only the delegated controllers among `cpu`/`memory` in
/// `R/cgroup.subtree_control`; controllers absent from `R/cgroup.controllers`
/// are skipped so degraded delegation never errors.
fn enable_subtree_controllers(root: &Path) {
    let available = std::fs::read_to_string(root.join("cgroup.controllers")).unwrap_or_default();
    let enable = REQUIRED_CONTROLLERS
        .into_iter()
        .filter(|controller| available.split_whitespace().any(|name| name == *controller))
        .map(|controller| format!("+{controller}"))
        .collect::<Vec<_>>()
        .join(" ");
    if !enable.is_empty() {
        let _ = std::fs::write(root.join("cgroup.subtree_control"), enable);
    }
}
