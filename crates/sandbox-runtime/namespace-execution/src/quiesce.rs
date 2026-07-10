use std::collections::BTreeSet;
use std::fs;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use nix::sys::signal::{kill, Signal};
use nix::unistd::Pid;

const FREEZE_POLL: Duration = Duration::from_micros(200);

/// One session's quiesce inputs, all daemon-owned facts.
#[derive(Debug, Clone)]
pub struct QuiesceSpec {
    pub holder_pid: i32,
    pub workspace_root: PathBuf,
    pub cgroup_procs_path: Option<PathBuf>,
    pub runner_pids: Vec<i32>,
    pub freeze_budget: Duration,
}

/// The three C1 branches: nothing to freeze, everything frozen with zero
/// pins, or blocked (already resumed) with a `class:detail` reason.
#[derive(Debug)]
pub enum QuiesceOutcome {
    NoObservableTasks {
        workspace_mount_id: u64,
    },
    Frozen {
        tasks: FrozenTasks,
        workspace_mount_id: u64,
    },
    Blocked {
        reason: String,
    },
}

/// Resume-on-drop guard over the frozen task set.
#[derive(Debug)]
pub struct FrozenTasks {
    pids: Vec<i32>,
}

impl FrozenTasks {
    #[must_use]
    pub fn pids(&self) -> &[i32] {
        &self.pids
    }
}

impl Drop for FrozenTasks {
    fn drop(&mut self) {
        for pid in &self.pids {
            let _ = kill(Pid::from_raw(*pid), Signal::SIGCONT);
        }
    }
}

/// Freeze and pin-inspect every task that can observe the holder mount
/// namespace. The holder mountinfo child-mount check runs first so it also
/// covers the no-observable-tasks branch (a child mount left by an exited
/// task still blocks).
#[must_use]
pub fn quiesce_holder_scope(spec: &QuiesceSpec) -> QuiesceOutcome {
    let workspace_mount_id = match check_holder_mounts(spec.holder_pid, &spec.workspace_root) {
        Ok(mount_id) => mount_id,
        Err(reason) => return QuiesceOutcome::Blocked { reason },
    };
    let Ok(holder_mnt) = fs::read_link(format!("/proc/{}/ns/mnt", spec.holder_pid)) else {
        return QuiesceOutcome::Blocked {
            reason: "mount_uncertain:proc_read_error".to_owned(),
        };
    };

    let discovered = match discover(spec, &holder_mnt) {
        Ok(discovered) => discovered,
        Err(reason) => return QuiesceOutcome::Blocked { reason },
    };
    if discovered.is_empty() {
        return QuiesceOutcome::NoObservableTasks { workspace_mount_id };
    }

    let mut frozen = Vec::new();
    for pid in &discovered {
        match kill(Pid::from_raw(*pid), Signal::SIGSTOP) {
            Ok(()) => frozen.push(*pid),
            Err(nix::errno::Errno::ESRCH) => {}
            Err(_) => {
                let _ = FrozenTasks { pids: frozen };
                return QuiesceOutcome::Blocked {
                    reason: "quiesce_failed:freeze_failed".to_owned(),
                };
            }
        }
    }
    let guard = FrozenTasks { pids: frozen };
    if guard.pids.is_empty() {
        return QuiesceOutcome::NoObservableTasks { workspace_mount_id };
    }

    let frozen_set: BTreeSet<i32> = guard.pids.iter().copied().collect();
    if let Err(reason) = poll_all_stopped(&guard.pids, &frozen_set, spec.freeze_budget) {
        return QuiesceOutcome::Blocked { reason };
    }

    match discover(spec, &holder_mnt) {
        Ok(recheck) => {
            if recheck.iter().any(|pid| !frozen_set.contains(pid)) {
                return QuiesceOutcome::Blocked {
                    reason: "quiesce_failed:membership_changed".to_owned(),
                };
            }
        }
        Err(reason) => return QuiesceOutcome::Blocked { reason },
    }

    for pid in &guard.pids {
        if let Err(reason) = inspect_frozen_pid(*pid, &holder_mnt, &spec.workspace_root) {
            return QuiesceOutcome::Blocked { reason };
        }
    }
    QuiesceOutcome::Frozen {
        tasks: guard,
        workspace_mount_id,
    }
}

/// One holder mountinfo re-read for the missing-report classification: the
/// workspace root's current mount id, `None` when the row is absent (a
/// runner died between the moves) or the read fails.
#[must_use]
pub fn workspace_mount_id(holder_pid: i32, workspace_root: &Path) -> Option<u64> {
    let mountinfo = fs::read_to_string(format!("/proc/{holder_pid}/mountinfo")).ok()?;
    let workspace = workspace_root.to_string_lossy();
    for line in mountinfo.lines() {
        if let Some((mount_id, mountpoint, _fstype)) = parse_mountinfo_line(line) {
            if mountpoint == *workspace {
                return Some(mount_id);
            }
        }
    }
    None
}

/// Union of the cgroup members and the `/proc` ns-scan, minus infrastructure
/// (the holder, its only direct child — the pid-ns init — and the runner
/// pids). A cgroup member outside the holder mount namespace is an escape.
fn discover(spec: &QuiesceSpec, holder_mnt: &Path) -> Result<BTreeSet<i32>, String> {
    let mut allow: BTreeSet<i32> = spec.runner_pids.iter().copied().collect();
    allow.insert(spec.holder_pid);
    let mut candidates: BTreeSet<i32> = BTreeSet::new();

    let proc_entries =
        fs::read_dir("/proc").map_err(|_| "mount_uncertain:proc_read_error".to_owned())?;
    for entry in proc_entries {
        let Ok(entry) = entry else { continue };
        let name = entry.file_name();
        let Some(pid) = name.to_str().and_then(|name| name.parse::<i32>().ok()) else {
            continue;
        };
        let Ok(ns) = fs::read_link(format!("/proc/{pid}/ns/mnt")) else {
            continue;
        };
        if ns == holder_mnt {
            candidates.insert(pid);
        }
    }

    if let Some(path) = &spec.cgroup_procs_path {
        let procs = fs::read_to_string(path)
            .map_err(|error| format!("quiesce_failed:cgroup_unreadable_{}", error.kind()))?;
        for line in procs.lines() {
            let Ok(pid) = line.trim().parse::<i32>() else {
                continue;
            };
            match fs::read_link(format!("/proc/{pid}/ns/mnt")) {
                Ok(ns) if ns == holder_mnt => {
                    candidates.insert(pid);
                }
                Ok(_) => return Err("pinned:mount_namespace_escaped".to_owned()),
                Err(_) => {}
            }
        }
    }

    for pid in &candidates {
        if parent_pid(*pid) == Some(spec.holder_pid) {
            allow.insert(*pid);
        }
    }
    Ok(candidates.difference(&allow).copied().collect())
}

fn path_pins_workspace(path: &Path, workspace_root: &Path) -> bool {
    path.starts_with(workspace_root)
}

fn parent_pid(pid: i32) -> Option<i32> {
    let stat = fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    let after_comm = stat.rsplit_once(')')?.1;
    after_comm.split_whitespace().nth(1)?.parse().ok()
}

pub(crate) fn task_state(stat: &str) -> Option<char> {
    stat.rsplit_once(')')?.1.trim_start().chars().next()
}

pub(crate) fn poll_all_stopped(
    pids: &[i32],
    frozen_set: &BTreeSet<i32>,
    budget: Duration,
) -> Result<(), String> {
    let deadline = Instant::now() + budget;
    loop {
        let mut all_stopped = true;
        for pid in pids {
            for tid in list_tids(*pid) {
                let Ok(stat) = fs::read_to_string(format!("/proc/{pid}/task/{tid}/stat")) else {
                    continue;
                };
                match task_state(&stat) {
                    Some('T') | Some('Z') | Some('X') | None => {}
                    Some('t') => {
                        if !tracer_in_set(*pid, tid, frozen_set) {
                            return Err("quiesce_failed:tracer_outside_frozen_set".to_owned());
                        }
                    }
                    Some(_) => {
                        all_stopped = false;
                    }
                }
            }
        }
        if all_stopped {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err("quiesce_failed:freeze_timeout".to_owned());
        }
        std::thread::sleep(FREEZE_POLL);
    }
}

fn list_tids(pid: i32) -> Vec<i32> {
    let Ok(entries) = fs::read_dir(format!("/proc/{pid}/task")) else {
        return Vec::new();
    };
    entries
        .filter_map(Result::ok)
        .filter_map(|entry| {
            entry
                .file_name()
                .to_str()
                .and_then(|name| name.parse().ok())
        })
        .collect()
}

fn tracer_in_set(pid: i32, tid: i32, frozen_set: &BTreeSet<i32>) -> bool {
    let Ok(status) = fs::read_to_string(format!("/proc/{pid}/task/{tid}/status")) else {
        return false;
    };
    status
        .lines()
        .find_map(|line| line.strip_prefix("TracerPid:"))
        .and_then(|value| value.trim().parse::<i32>().ok())
        .is_some_and(|tracer| frozen_set.contains(&tracer))
}

/// C4 pin inspection of one frozen process, every task: `ns/mnt` equality,
/// `cwd`, `root`, open fds, and `maps`. Any read error is uncertainty and
/// blocks.
fn inspect_frozen_pid(pid: i32, holder_mnt: &Path, workspace_root: &Path) -> Result<(), String> {
    for tid in list_tids(pid) {
        let task = format!("/proc/{pid}/task/{tid}");
        let ns = fs::read_link(format!("{task}/ns/mnt"))
            .map_err(|_| "mount_uncertain:proc_read_error".to_owned())?;
        if ns != holder_mnt {
            return Err("pinned:mount_namespace_escaped".to_owned());
        }
        let cwd = fs::read_link(format!("{task}/cwd"))
            .map_err(|_| "mount_uncertain:proc_read_error".to_owned())?;
        if path_pins_workspace(&cwd, workspace_root) {
            return Err("pinned:cwd_pinned_workspace".to_owned());
        }
        let root = fs::read_link(format!("{task}/root"))
            .map_err(|_| "mount_uncertain:proc_read_error".to_owned())?;
        if path_pins_workspace(&root, workspace_root) {
            return Err("pinned:root_pinned_workspace".to_owned());
        }
        inspect_fds(&task, workspace_root)?;
        inspect_maps(&task, workspace_root)?;
    }
    Ok(())
}

fn inspect_fds(task: &str, workspace_root: &Path) -> Result<(), String> {
    let entries = fs::read_dir(format!("{task}/fd"))
        .map_err(|_| "mount_uncertain:proc_read_error".to_owned())?;
    for entry in entries {
        let entry = entry.map_err(|_| "mount_uncertain:proc_read_error".to_owned())?;
        let link = match fs::read_link(entry.path()) {
            Ok(link) => link,
            Err(error) if error.kind() == ErrorKind::NotFound => continue,
            Err(_) => return Err("mount_uncertain:proc_read_error".to_owned()),
        };
        let text = link.to_string_lossy();
        if let Some(kind) = text
            .strip_prefix("anon_inode:[")
            .and_then(|rest| rest.strip_suffix(']'))
        {
            match kind {
                "eventfd" | "timerfd" => {}
                other => {
                    return Err(format!(
                        "pinned:anon_inode_{}",
                        other.replace(|c: char| !c.is_ascii_alphanumeric(), "_")
                    ))
                }
            }
            continue;
        }
        if text.starts_with("socket:[") || text.starts_with("pipe:[") {
            continue;
        }
        if text.starts_with("/dev/pts/") {
            continue;
        }
        if path_pins_workspace(&link, workspace_root) {
            return Err("pinned:fd_pinned_workspace".to_owned());
        }
    }
    Ok(())
}

fn inspect_maps(task: &str, workspace_root: &Path) -> Result<(), String> {
    let maps = fs::read_to_string(format!("{task}/maps"))
        .map_err(|_| "mount_uncertain:proc_read_error".to_owned())?;
    for line in maps.lines() {
        let mut fields = line.splitn(6, char::is_whitespace);
        let parsed = (
            fields.next(),
            fields.next(),
            fields.next(),
            fields.next(),
            fields.next(),
        );
        if parsed.4.is_none() {
            return Err("pinned:mapped_file_unparsable".to_owned());
        }
        let path = fields.next().unwrap_or("").trim_start();
        if path.is_empty() || path.starts_with('[') {
            continue;
        }
        if path.ends_with("(deleted)") {
            return Err("pinned:mapped_file_deleted".to_owned());
        }
        if path_pins_workspace(Path::new(path), workspace_root) {
            return Err("pinned:mapped_file_pinned_workspace".to_owned());
        }
    }
    Ok(())
}

/// ONE holder mountinfo read: the workspace root must be an overlay mount
/// and nothing may be mounted strictly under it (masks are namespace-root
/// tmpfs, not workspace children).
fn check_holder_mounts(holder_pid: i32, workspace_root: &Path) -> Result<u64, String> {
    let mountinfo = fs::read_to_string(format!("/proc/{holder_pid}/mountinfo"))
        .map_err(|_| "mount_uncertain:mountinfo_unavailable".to_owned())?;
    let workspace = workspace_root.to_string_lossy();
    let child_prefix = format!("{workspace}/");
    let mut workspace_overlay_id = None;
    for line in mountinfo.lines() {
        let Some((mount_id, mountpoint, fstype)) = parse_mountinfo_line(line) else {
            return Err("mount_uncertain:mountinfo_mismatch".to_owned());
        };
        if mountpoint == *workspace {
            workspace_overlay_id = (fstype == "overlay").then_some(mount_id);
        } else if mountpoint.starts_with(&child_prefix) {
            return Err("pinned:child_mount_pinned_workspace".to_owned());
        }
    }
    workspace_overlay_id.ok_or_else(|| "mount_uncertain:mountinfo_mismatch".to_owned())
}

pub(crate) fn parse_mountinfo_line(line: &str) -> Option<(u64, String, String)> {
    let mut fields = line.split(' ');
    let mount_id: u64 = fields.next()?.parse().ok()?;
    let _parent_id = fields.next()?;
    let _major_minor = fields.next()?;
    let _root = fields.next()?;
    let mountpoint = octal_unescape(fields.next()?);
    let mut fields = fields.skip_while(|field| *field != "-");
    let _separator = fields.next()?;
    let fstype = fields.next()?.to_owned();
    Some((mount_id, mountpoint, fstype))
}

pub(crate) fn octal_unescape(escaped: &str) -> String {
    let mut out = String::with_capacity(escaped.len());
    let mut chars = escaped.chars();
    while let Some(ch) = chars.next() {
        if ch != '\\' {
            out.push(ch);
            continue;
        }
        let digits: String = chars.by_ref().take(3).collect();
        match u8::from_str_radix(&digits, 8) {
            Ok(byte) => out.push(char::from(byte)),
            Err(_) => {
                out.push('\\');
                out.push_str(&digits);
            }
        }
    }
    out
}
