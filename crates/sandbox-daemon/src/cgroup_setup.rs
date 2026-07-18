//! Daemon-side cgroup v2 root discovery and self-vacation.
//!
//! The daemon owns only the discovery of its delegated cgroup root `R` and the
//! move of its own processes into `R/_daemon` so `R` can enable controllers for
//! the per-workspace child cgroups the runtime creates. Everything here is
//! fail-safe: any failure disables workload cgroups with a concrete reason
//! while leaving the daemon available for cleanup.

use std::path::{Path, PathBuf};

const CGROUP_FS_ROOT: &str = "/sys/fs/cgroup";
const DAEMON_LEAF: &str = "_daemon";
const WORKLOADS_SUBTREE: &str = "_workloads";
const REQUIRED_CONTROLLERS: [&str; 3] = ["cpu", "memory", "pids"];
const DAEMON_MEMORY_PROTECTION_BYTES: u64 = 32 * 1024 * 1024;
const DAEMON_CPU_WEIGHT: u16 = 10_000;
const WORKLOAD_CPU_WEIGHT: u16 = 100;

pub(crate) type WorkloadCgroupSettings = sandbox_runtime::WorkloadCgroupLimits;

/// Discover the delegated cgroup v2 root `R`, vacate the daemon into
/// `R/_daemon`, and create a bounded `R/_workloads` subtree. The returned path
/// is the aggregate workload subtree, not `R`: every per-workspace leaf is
/// created below it so several individually bounded workspaces cannot consume
/// the daemon's outer-container memory or PID reserve in aggregate.
pub(crate) fn discover_and_prepare_root(
    settings: WorkloadCgroupSettings,
) -> Result<PathBuf, String> {
    let proc_self_cgroup = std::fs::read_to_string("/proc/self/cgroup")
        .map_err(|error| format!("read /proc/self/cgroup: {error}"))?;
    let root = parse_cgroup_root(&proc_self_cgroup)
        .ok_or_else(|| "unified cgroup v2 membership is unavailable".to_owned())?;
    prepare_root(&root, settings)
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

pub(crate) fn prepare_root(
    root: &Path,
    settings: WorkloadCgroupSettings,
) -> Result<PathBuf, String> {
    let daemon_leaf = root.join(DAEMON_LEAF);
    std::fs::create_dir_all(&daemon_leaf)
        .map_err(|error| format!("create {}: {error}", daemon_leaf.display()))?;
    std::fs::write(
        daemon_leaf.join("cgroup.procs"),
        std::process::id().to_string(),
    )
    .map_err(|error| format!("move daemon into {}: {error}", daemon_leaf.display()))?;
    enable_subtree_controllers(root)?;
    std::fs::write(
        root.join("memory.high"),
        settings.memory_high_bytes.to_string(),
    )
    .map_err(|error| format!("set outer memory.high: {error}"))?;
    let protection = DAEMON_MEMORY_PROTECTION_BYTES.to_string();
    std::fs::write(daemon_leaf.join("memory.min"), &protection)
        .map_err(|error| format!("set daemon memory.min: {error}"))?;
    std::fs::write(daemon_leaf.join("memory.low"), protection)
        .map_err(|error| format!("set daemon memory.low: {error}"))?;
    std::fs::write(
        daemon_leaf.join("cpu.weight"),
        DAEMON_CPU_WEIGHT.to_string(),
    )
    .map_err(|error| format!("set daemon cpu.weight: {error}"))?;

    let workloads = root.join(WORKLOADS_SUBTREE);
    std::fs::create_dir_all(&workloads)
        .map_err(|error| format!("create {}: {error}", workloads.display()))?;
    std::fs::write(
        workloads.join("cpu.weight"),
        WORKLOAD_CPU_WEIGHT.to_string(),
    )
    .map_err(|error| format!("set aggregate workload cpu.weight: {error}"))?;
    write_workload_limits(&workloads, settings)?;
    enable_subtree_controllers(&workloads)?;
    Ok(workloads)
}

fn write_workload_limits(workloads: &Path, settings: WorkloadCgroupSettings) -> Result<(), String> {
    const CPU_PERIOD_US: u128 = 100_000;
    const NANOS_PER_CPU: u128 = 1_000_000_000;
    let quota = (u128::from(settings.nano_cpus) * CPU_PERIOD_US).div_ceil(NANOS_PER_CPU);
    let quota = u64::try_from(quota).unwrap_or(u64::MAX).max(1);
    let writes = [
        ("cpu.max", format!("{quota} {CPU_PERIOD_US}")),
        ("memory.high", settings.memory_high_bytes.to_string()),
        ("memory.max", settings.memory_max_bytes.to_string()),
        ("memory.oom.group", "1".to_owned()),
        ("pids.max", settings.pids_max.to_string()),
    ];
    for (name, value) in writes {
        let path = workloads.join(name);
        std::fs::write(&path, value)
            .map_err(|error| format!("set aggregate workload {}: {error}", path.display()))?;
    }
    Ok(())
}

/// Enable only the delegated controllers among `cpu`/`memory`/`pids` in
/// `R/cgroup.subtree_control`; controllers absent from `R/cgroup.controllers`
/// are skipped so degraded delegation never errors.
fn enable_subtree_controllers(root: &Path) -> Result<(), String> {
    let controllers_path = root.join("cgroup.controllers");
    let available = std::fs::read_to_string(&controllers_path)
        .map_err(|error| format!("read {}: {error}", controllers_path.display()))?;
    let missing = REQUIRED_CONTROLLERS
        .into_iter()
        .filter(|controller| !available.split_whitespace().any(|name| name == *controller))
        .collect::<Vec<_>>();
    if !missing.is_empty() {
        return Err(format!(
            "delegated cgroup root lacks required controllers: {}",
            missing.join(",")
        ));
    }
    let enable = enabled_controller_directives(&available);
    let subtree_path = root.join("cgroup.subtree_control");
    std::fs::write(&subtree_path, enable)
        .map_err(|error| format!("enable controllers in {}: {error}", subtree_path.display()))
}

pub(crate) fn enabled_controller_directives(available: &str) -> String {
    REQUIRED_CONTROLLERS
        .into_iter()
        .filter(|controller| available.split_whitespace().any(|name| name == *controller))
        .map(|controller| format!("+{controller}"))
        .collect::<Vec<_>>()
        .join(" ")
}
