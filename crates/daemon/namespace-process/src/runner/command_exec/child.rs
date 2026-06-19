use std::fs;
use std::io::Read;
use std::os::fd::BorrowedFd;
use std::thread;
use std::time::{Duration, Instant};

use rustix::process::{getpgrp, kill_process_group, Pid, Signal};

use crate::runner::RunnerError;

const CHILD_WAIT_POLL: Duration = Duration::from_millis(5);

#[derive(Debug, Clone, Default)]
pub(super) struct CommandExecutionScopeTiming {
    pub(super) root_exit_s: Option<f64>,
    pub(super) post_root_drain_s: Option<f64>,
    pub(super) child_try_wait_s: f64,
    pub(super) proc_scan_s: f64,
    pub(super) proc_scan_count: u64,
    pub(super) poll_count: u64,
    pub(super) poll_sleep_s: f64,
}

pub(super) fn wait_for_command_execution_scope(
    child: &mut std::process::Child,
    timeout_seconds: Option<f64>,
    proc_dir: Option<BorrowedFd>,
) -> Result<(i32, CommandExecutionScopeTiming), RunnerError> {
    let started = Instant::now();
    let deadline = timeout_deadline(timeout_seconds);
    let pgid = getpgrp().as_raw_nonzero().get();
    let self_pid = i32::try_from(std::process::id()).unwrap_or(i32::MAX);
    let mut timing = CommandExecutionScopeTiming::default();
    let mut root_exit_code = None;
    let mut root_exit_at = None;
    loop {
        if root_exit_code.is_none() {
            let try_wait_start = Instant::now();
            if let Some(status) = child.try_wait().map_err(RunnerError::Child)? {
                let now = Instant::now();
                root_exit_code = Some(exit_code(status));
                root_exit_at = Some(now);
                timing.root_exit_s = Some(now.duration_since(started).as_secs_f64());
            }
            timing.child_try_wait_s += try_wait_start.elapsed().as_secs_f64();
        }
        if root_exit_code.is_some() {
            let proc_scan_start = Instant::now();
            let has_other_live_members =
                process_group_has_other_live_members(pgid, self_pid, proc_dir);
            timing.proc_scan_s += proc_scan_start.elapsed().as_secs_f64();
            timing.proc_scan_count += 1;
            if !has_other_live_members {
                timing.post_root_drain_s = root_exit_at.map(|at| at.elapsed().as_secs_f64());
                return Ok((root_exit_code.unwrap_or(0), timing));
            }
        }
        if deadline.is_some_and(|deadline| Instant::now() >= deadline) {
            if let Some(pid) = Pid::from_raw(pgid) {
                let _ = kill_process_group(pid, Signal::Kill);
            }
            let _ = child.wait();
            return Err(RunnerError::TimedOut);
        }
        let sleep_start = Instant::now();
        thread::sleep(CHILD_WAIT_POLL);
        timing.poll_count += 1;
        timing.poll_sleep_s += sleep_start.elapsed().as_secs_f64();
    }
}

fn timeout_deadline(timeout_seconds: Option<f64>) -> Option<Instant> {
    timeout_seconds
        .filter(|seconds| seconds.is_finite() && *seconds >= 0.0)
        .map(|seconds| Instant::now() + Duration::from_secs_f64(seconds))
}

fn exit_code(status: std::process::ExitStatus) -> i32 {
    use std::os::unix::process::ExitStatusExt;

    status
        .code()
        .or_else(|| status.signal().map(|sig| -sig))
        .unwrap_or(128)
}

fn process_group_has_other_live_members(
    pgid: i32,
    self_pid: i32,
    proc_dir: Option<BorrowedFd>,
) -> bool {
    let Some(proc_dir) = proc_dir else {
        return process_group_has_other_live_members_by_path(pgid, self_pid);
    };
    let Ok(dir) = rustix::fs::Dir::read_from(proc_dir) else {
        return false;
    };
    for entry in dir {
        let Ok(entry) = entry else { continue };
        let Some(pid) = entry
            .file_name()
            .to_str()
            .ok()
            .and_then(|name| name.parse::<i32>().ok())
        else {
            continue;
        };
        if pid == self_pid {
            continue;
        }
        if proc_stat_process_group_at(proc_dir, pid)
            .is_some_and(|(entry_pgid, state)| entry_pgid == pgid && state != 'Z')
        {
            return true;
        }
    }
    false
}

fn process_group_has_other_live_members_by_path(pgid: i32, self_pid: i32) -> bool {
    let Ok(entries) = fs::read_dir("/proc") else {
        return false;
    };
    entries.filter_map(Result::ok).any(|entry| {
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|name| name.parse::<i32>().ok())
        else {
            return false;
        };
        if pid == self_pid {
            return false;
        }
        fs::read_to_string(format!("/proc/{pid}/stat"))
            .ok()
            .and_then(|stat| parse_proc_stat(&stat))
            .is_some_and(|(entry_pgid, state)| entry_pgid == pgid && state != 'Z')
    })
}

fn proc_stat_process_group_at(proc_dir: BorrowedFd, pid: i32) -> Option<(i32, char)> {
    let fd = rustix::fs::openat(
        proc_dir,
        format!("{pid}/stat"),
        rustix::fs::OFlags::RDONLY | rustix::fs::OFlags::CLOEXEC,
        rustix::fs::Mode::empty(),
    )
    .ok()?;
    let mut stat = String::new();
    fs::File::from(fd).read_to_string(&mut stat).ok()?;
    parse_proc_stat(&stat)
}

fn parse_proc_stat(stat: &str) -> Option<(i32, char)> {
    let close = stat.rfind(") ")?;
    let fields: Vec<&str> = stat[close + 2..].split_whitespace().collect();
    let state = fields.first()?.chars().next()?;
    let pgrp = fields.get(2)?.parse::<i32>().ok()?;
    Some((pgrp, state))
}
