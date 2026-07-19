//! Daemon-side cgroup v2 root discovery and self-vacation.
//!
//! The daemon owns only the discovery of its delegated cgroup root `R` and the
//! move of its own processes into `R/_daemon` so `R` can enable controllers for
//! the per-workspace child cgroups the runtime creates. Everything here is
//! fail-safe: any failure disables workload cgroups with a concrete reason
//! while leaving the daemon available for cleanup.

use std::collections::HashSet;
use std::fs::File;
use std::io::Read;
use std::path::{Path, PathBuf};

use sandbox_runtime_namespace_process::pid_identity::{pin_pid_identity, PidIdentityGuard};

const CGROUP_FS_ROOT: &str = "/sys/fs/cgroup";
const DAEMON_LEAF: &str = "_daemon";
const WORKLOADS_SUBTREE: &str = "_workloads";
const REQUIRED_CONTROLLERS: [&str; 3] = ["cpu", "memory", "pids"];
const DAEMON_MEMORY_PROTECTION_BYTES: u64 = 32 * 1024 * 1024;
const DAEMON_CPU_WEIGHT: u16 = 10_000;
const WORKLOAD_CPU_WEIGHT: u16 = 100;
pub(crate) const DIRECT_ROOT_PROCS_MAX_BYTES: usize = 64 * 1024;
pub(crate) const DIRECT_ROOT_PROCS_MAX_PIDS: usize = 4_096;
const PROC_STAT_MAX_BYTES: usize = 4 * 1024;
const PROC_CGROUP_MAX_BYTES: usize = 16 * 1024;

pub(crate) type WorkloadCgroupSettings = sandbox_runtime::WorkloadCgroupLimits;

/// Discover the delegated cgroup v2 root `R`, vacate the daemon into
/// `R/_daemon`, and create an aggregate `R/_workloads` subtree. The returned
/// path is the aggregate workload subtree, not `R`: every per-workspace leaf is
/// created below it so daemon protection and aggregate CPU/PID limits remain
/// separate from per-workspace hard memory and OOM isolation.
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
    let mut process_ops = SystemRootProcessOps::new(PathBuf::from("/proc"));
    prepare_root_with_process_ops(root, settings, &mut process_ops)
}

pub(crate) fn prepare_root_with_process_ops<O: RootProcessOps>(
    root: &Path,
    settings: WorkloadCgroupSettings,
    process_ops: &mut O,
) -> Result<PathBuf, String> {
    let daemon_leaf = root.join(DAEMON_LEAF);
    std::fs::create_dir_all(&daemon_leaf)
        .map_err(|error| format!("create {}: {error}", daemon_leaf.display()))?;
    evacuate_direct_root_processes(root, &daemon_leaf, process_ops)?;
    enable_subtree_controllers(root)?;
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
    write_aggregate_workload_policy(&workloads, settings)?;
    enable_subtree_controllers(&workloads)?;
    Ok(workloads)
}

fn write_aggregate_workload_policy(
    workloads: &Path,
    settings: WorkloadCgroupSettings,
) -> Result<(), String> {
    const CPU_PERIOD_US: u128 = 100_000;
    const NANOS_PER_CPU: u128 = 1_000_000_000;
    let quota = (u128::from(settings.nano_cpus) * CPU_PERIOD_US).div_ceil(NANOS_PER_CPU);
    let quota = u64::try_from(quota).unwrap_or(u64::MAX).max(1);
    let writes = [
        ("cpu.max", format!("{quota} {CPU_PERIOD_US}")),
        ("memory.high", settings.memory_high_bytes.to_string()),
        ("memory.max", settings.memory_max_bytes.to_string()),
        ("memory.oom.group", "0".to_owned()),
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
pub(crate) fn enable_subtree_controllers(root: &Path) -> Result<(), String> {
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

/// Abstracts the PID-sensitive part of cgroup self-vacation so tests can prove
/// ordering, race handling, and bounds without pretending a temporary
/// directory has kernel `cgroup.procs` migration semantics.
pub(crate) trait RootProcessOps {
    type PinnedProcess;

    fn read_direct_pids(
        &mut self,
        root: &Path,
        max_bytes: usize,
        max_pids: usize,
    ) -> Result<Vec<u32>, String>;

    fn pin_process(
        &mut self,
        pid: u32,
        expected_cgroup: &Path,
    ) -> Result<Self::PinnedProcess, String>;

    fn move_process(
        &mut self,
        process: &Self::PinnedProcess,
        expected_cgroup: &Path,
        target_cgroup: &Path,
    ) -> Result<(), String>;

    fn validate_current_process(&mut self, expected_cgroup: &Path) -> Result<(), String>;
}

/// Vacate every process directly attached to `root` before enabling domain
/// controllers. All identities are pinned before the first migration, each
/// `cgroup.procs` write contains exactly one PID, and a bounded second read
/// rejects any residual or newly raced process.
pub(crate) fn evacuate_direct_root_processes<O: RootProcessOps>(
    root: &Path,
    daemon_leaf: &Path,
    process_ops: &mut O,
) -> Result<(), String> {
    let direct_pids = process_ops.read_direct_pids(
        root,
        DIRECT_ROOT_PROCS_MAX_BYTES,
        DIRECT_ROOT_PROCS_MAX_PIDS,
    )?;
    validate_distinct_pids(&direct_pids)?;

    let mut pinned = Vec::with_capacity(direct_pids.len());
    for pid in direct_pids {
        let process = process_ops
            .pin_process(pid, root)
            .map_err(|error| format!("pin direct cgroup process {pid}: {error}"))?;
        pinned.push(process);
    }

    for process in &pinned {
        process_ops.move_process(process, root, daemon_leaf)?;
    }

    let residual = process_ops.read_direct_pids(
        root,
        DIRECT_ROOT_PROCS_MAX_BYTES,
        DIRECT_ROOT_PROCS_MAX_PIDS,
    )?;
    validate_distinct_pids(&residual)?;
    if !residual.is_empty() {
        return Err(format!(
            "delegated cgroup root {} still has direct processes after evacuation: {}",
            root.display(),
            bounded_pid_summary(&residual)
        ));
    }

    process_ops
        .validate_current_process(daemon_leaf)
        .map_err(|error| format!("validate daemon cgroup membership: {error}"))
}

fn validate_distinct_pids(pids: &[u32]) -> Result<(), String> {
    if pids.len() > DIRECT_ROOT_PROCS_MAX_PIDS {
        return Err(format!(
            "direct cgroup PID count exceeds the {} PID count bound",
            DIRECT_ROOT_PROCS_MAX_PIDS
        ));
    }
    let mut distinct = HashSet::with_capacity(pids.len());
    for &pid in pids {
        if pid == 0 || pid > i32::MAX as u32 {
            return Err(format!("direct cgroup process has invalid PID {pid}"));
        }
        if !distinct.insert(pid) {
            return Err(format!("direct cgroup process list repeats PID {pid}"));
        }
    }
    Ok(())
}

fn bounded_pid_summary(pids: &[u32]) -> String {
    const DISPLAY_LIMIT: usize = 16;
    let mut summary = pids
        .iter()
        .take(DISPLAY_LIMIT)
        .map(u32::to_string)
        .collect::<Vec<_>>()
        .join(",");
    if pids.len() > DISPLAY_LIMIT {
        summary.push_str(&format!(",... ({} total)", pids.len()));
    }
    summary
}

pub(crate) fn read_direct_root_pids_file(
    path: &Path,
    max_bytes: usize,
    max_pids: usize,
) -> Result<Vec<u32>, String> {
    let bytes = read_bounded(path, max_bytes)?;
    let text = std::str::from_utf8(&bytes)
        .map_err(|_| format!("{} is not valid UTF-8", path.display()))?;
    let mut pids = Vec::new();
    for raw in text.lines() {
        let value = raw.trim();
        if value.is_empty() {
            continue;
        }
        if pids.len() == max_pids {
            return Err(format!(
                "{} exceeds the {} PID count bound",
                path.display(),
                max_pids
            ));
        }
        let pid = value
            .parse::<u32>()
            .ok()
            .filter(|pid| *pid > 0 && *pid <= i32::MAX as u32)
            .ok_or_else(|| format!("{} contains invalid PID {value:?}", path.display()))?;
        pids.push(pid);
    }
    validate_distinct_pids(&pids)?;
    Ok(pids)
}

fn read_bounded(path: &Path, max_bytes: usize) -> Result<Vec<u8>, String> {
    let mut bytes = Vec::new();
    File::open(path)
        .and_then(|file| {
            file.take(
                u64::try_from(max_bytes)
                    .unwrap_or(u64::MAX)
                    .saturating_add(1),
            )
            .read_to_end(&mut bytes)
        })
        .map_err(|error| format!("read {}: {error}", path.display()))?;
    if bytes.len() > max_bytes {
        return Err(format!(
            "{} exceeds the {} byte read bound",
            path.display(),
            max_bytes
        ));
    }
    Ok(bytes)
}

struct SystemRootProcessOps {
    proc_root: PathBuf,
}

impl SystemRootProcessOps {
    fn new(proc_root: PathBuf) -> Self {
        Self { proc_root }
    }

    fn process_start_time(&self, pid: u32) -> Result<u64, String> {
        let path = self.proc_root.join(pid.to_string()).join("stat");
        let bytes = read_bounded(&path, PROC_STAT_MAX_BYTES)?;
        let stat = std::str::from_utf8(&bytes)
            .map_err(|_| format!("{} is not valid UTF-8", path.display()))?;
        parse_start_time_ticks(stat)
            .ok_or_else(|| format!("{} lacks a valid start-time field", path.display()))
    }

    fn process_cgroup(&self, pid: u32) -> Result<PathBuf, String> {
        let path = self.proc_root.join(pid.to_string()).join("cgroup");
        let bytes = read_bounded(&path, PROC_CGROUP_MAX_BYTES)?;
        let membership = std::str::from_utf8(&bytes)
            .map_err(|_| format!("{} is not valid UTF-8", path.display()))?;
        parse_cgroup_root(membership)
            .ok_or_else(|| format!("{} lacks unified cgroup v2 membership", path.display()))
    }

    fn validate_identity(
        &self,
        process: &PinnedRootProcess,
        expected_cgroup: &Path,
    ) -> Result<(), String> {
        let start_time = self.process_start_time(process.pid)?;
        if start_time != process.start_time_ticks {
            return Err(format!(
                "PID {} identity changed before migration: start time {} became {}",
                process.pid, process.start_time_ticks, start_time
            ));
        }
        let cgroup = self.process_cgroup(process.pid)?;
        if cgroup != expected_cgroup {
            return Err(format!(
                "PID {} moved from expected cgroup {} to {}",
                process.pid,
                expected_cgroup.display(),
                cgroup.display()
            ));
        }
        Ok(())
    }
}

struct PinnedRootProcess {
    pid: u32,
    start_time_ticks: u64,
    // Holding the pidfd pins the kernel PID identity across all numeric
    // cgroup.procs writes; start time and membership are still rechecked
    // immediately before and after migration for fail-closed diagnostics.
    _pidfd: PidIdentityGuard,
}

impl RootProcessOps for SystemRootProcessOps {
    type PinnedProcess = PinnedRootProcess;

    fn read_direct_pids(
        &mut self,
        root: &Path,
        max_bytes: usize,
        max_pids: usize,
    ) -> Result<Vec<u32>, String> {
        read_direct_root_pids_file(&root.join("cgroup.procs"), max_bytes, max_pids)
    }

    fn pin_process(
        &mut self,
        pid: u32,
        expected_cgroup: &Path,
    ) -> Result<Self::PinnedProcess, String> {
        let pidfd = pin_pid_identity(pid)?;
        let process = PinnedRootProcess {
            pid,
            start_time_ticks: self.process_start_time(pid)?,
            _pidfd: pidfd,
        };
        self.validate_identity(&process, expected_cgroup)?;
        Ok(process)
    }

    fn move_process(
        &mut self,
        process: &Self::PinnedProcess,
        expected_cgroup: &Path,
        target_cgroup: &Path,
    ) -> Result<(), String> {
        self.validate_identity(process, expected_cgroup)?;
        let target = target_cgroup.join("cgroup.procs");
        std::fs::write(&target, process.pid.to_string()).map_err(|error| {
            format!(
                "move PID {} through {}: {error}",
                process.pid,
                target.display()
            )
        })?;
        self.validate_identity(process, target_cgroup)
            .map_err(|error| format!("validate migrated PID {}: {error}", process.pid))
    }

    fn validate_current_process(&mut self, expected_cgroup: &Path) -> Result<(), String> {
        let pid = std::process::id();
        let process = self.pin_process(pid, expected_cgroup)?;
        self.validate_identity(&process, expected_cgroup)
    }
}

fn parse_start_time_ticks(stat: &str) -> Option<u64> {
    let after_name = stat.rsplit_once(") ")?.1;
    after_name.split_whitespace().nth(19)?.parse().ok()
}

pub(crate) fn enabled_controller_directives(available: &str) -> String {
    REQUIRED_CONTROLLERS
        .into_iter()
        .filter(|controller| available.split_whitespace().any(|name| name == *controller))
        .map(|controller| format!("+{controller}"))
        .collect::<Vec<_>>()
        .join(" ")
}
