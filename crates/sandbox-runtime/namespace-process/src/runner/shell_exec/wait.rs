use std::fs;
use std::io::Read;
use std::os::fd::BorrowedFd;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use nix::sys::signal::{kill, sigaction, SaFlags, SigAction, SigHandler, SigSet, Signal};
use nix::unistd::Pid;

use crate::runner::RunnerError;

const CHILD_WAIT_POLL: Duration = Duration::from_millis(5);
const TERMINATE_GRACE: Duration = Duration::from_millis(100);
const KILL_GRACE: Duration = Duration::from_secs(1);
const CANCELLED_EXIT_CODE: i32 = 130;
static TERMINATION_REQUESTED: AtomicBool = AtomicBool::new(false);

pub(super) fn install_termination_signal_handlers() -> Result<(), RunnerError> {
    let action = SigAction::new(
        SigHandler::Handler(mark_termination_requested),
        SaFlags::empty(),
        SigSet::empty(),
    );
    // SAFETY: the handler only stores to an atomic flag and returns.
    unsafe {
        sigaction(Signal::SIGTERM, &action).map_err(signal_error)?;
        sigaction(Signal::SIGINT, &action).map_err(signal_error)?;
    }
    Ok(())
}

pub(super) fn wait_for_command_execution_scope(
    child: &mut std::process::Child,
    child_pgid: i32,
    timeout_seconds: Option<f64>,
    proc_dir: Option<BorrowedFd>,
) -> Result<i32, RunnerError> {
    let deadline = timeout_deadline(timeout_seconds);
    let self_pid = i32::try_from(std::process::id()).unwrap_or(i32::MAX);
    let child_pid = child_process_id(child)?;
    let mut root_exit_code = None;
    loop {
        if root_exit_code.is_none() {
            if let Some(status) = child.try_wait().map_err(RunnerError::Child)? {
                root_exit_code = Some(exit_code(status));
            }
        }
        if root_exit_code.is_some() {
            let has_other_live_members =
                pgid_has_other_live_members(child_pgid, self_pid, proc_dir);
            if !has_other_live_members {
                return Ok(root_exit_code.unwrap_or(0));
            }
        }
        if TERMINATION_REQUESTED.load(Ordering::Relaxed) {
            terminate_execution_scope(child, child_pid, child_pgid, self_pid, proc_dir);
            return Ok(CANCELLED_EXIT_CODE);
        }
        if deadline.is_some_and(|deadline| Instant::now() >= deadline) {
            terminate_execution_scope(child, child_pid, child_pgid, self_pid, proc_dir);
            return Err(RunnerError::TimedOut);
        }
        thread::sleep(CHILD_WAIT_POLL);
    }
}

extern "C" fn mark_termination_requested(_signal: libc::c_int) {
    TERMINATION_REQUESTED.store(true, Ordering::Relaxed);
}

fn signal_error(error: nix::errno::Errno) -> RunnerError {
    RunnerError::Syscall(std::io::Error::from_raw_os_error(error as i32))
}

fn child_process_id(child: &std::process::Child) -> Result<i32, RunnerError> {
    i32::try_from(child.id()).map_err(|_| {
        RunnerError::InvalidRequest(format!("child pid does not fit i32: {}", child.id()))
    })
}

fn terminate_execution_scope(
    child: &mut std::process::Child,
    child_pid: i32,
    child_pgid: i32,
    self_pid: i32,
    proc_dir: Option<BorrowedFd>,
) {
    signal_execution_scope(child_pid, child_pgid, self_pid, proc_dir, Signal::SIGTERM);
    if wait_for_scope_exit(child, child_pgid, self_pid, proc_dir, TERMINATE_GRACE) {
        return;
    }
    signal_execution_scope(child_pid, child_pgid, self_pid, proc_dir, Signal::SIGKILL);
    if wait_for_scope_exit(child, child_pgid, self_pid, proc_dir, KILL_GRACE) {
        return;
    }
    let _ = child.kill();
    let _ = child.try_wait();
}

fn signal_execution_scope(
    child_pid: i32,
    child_pgid: i32,
    self_pid: i32,
    proc_dir: Option<BorrowedFd>,
    signal: Signal,
) {
    let _ = kill(Pid::from_raw(-child_pgid), signal);
    let _ = kill(Pid::from_raw(child_pid), signal);
    for pid in live_pids_in_pgid(child_pgid, self_pid, proc_dir) {
        let _ = kill(Pid::from_raw(pid), signal);
    }
}

fn wait_for_scope_exit(
    child: &mut std::process::Child,
    child_pgid: i32,
    self_pid: i32,
    proc_dir: Option<BorrowedFd>,
    grace: Duration,
) -> bool {
    let deadline = Instant::now() + grace;
    loop {
        let child_done = child.try_wait().map_or(true, |status| status.is_some());
        let scope_done = !pgid_has_other_live_members(child_pgid, self_pid, proc_dir);
        if child_done && scope_done {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        thread::sleep(CHILD_WAIT_POLL);
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

fn pgid_has_other_live_members(pgid: i32, self_pid: i32, proc_dir: Option<BorrowedFd>) -> bool {
    !live_pids_in_pgid(pgid, self_pid, proc_dir).is_empty()
}

fn live_pids_in_pgid(pgid: i32, self_pid: i32, proc_dir: Option<BorrowedFd>) -> Vec<i32> {
    let Some(proc_dir) = proc_dir else {
        return live_pids_in_pgid_by_path(pgid, self_pid);
    };
    let Ok(dir) = rustix::fs::Dir::read_from(proc_dir) else {
        return Vec::new();
    };
    let mut pids = Vec::new();
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
        if proc_stat_pgid_at(proc_dir, pid)
            .is_some_and(|(entry_pgid, state)| entry_pgid == pgid && state != 'Z')
        {
            pids.push(pid);
        }
    }
    pids
}

fn live_pids_in_pgid_by_path(pgid: i32, self_pid: i32) -> Vec<i32> {
    let Ok(entries) = fs::read_dir("/proc") else {
        return Vec::new();
    };
    entries
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let pid = entry
                .file_name()
                .to_str()
                .and_then(|name| name.parse::<i32>().ok())?;
            (pid != self_pid)
                .then_some(pid)
                .filter(|pid| live_pid_is_in_pgid(*pid, pgid))
        })
        .collect()
}

fn live_pid_is_in_pgid(pid: i32, pgid: i32) -> bool {
    fs::read_to_string(format!("/proc/{pid}/stat"))
        .ok()
        .and_then(|stat| parse_proc_stat(&stat))
        .is_some_and(|(entry_pgid, state)| entry_pgid == pgid && state != 'Z')
}

fn proc_stat_pgid_at(proc_dir: BorrowedFd, pid: i32) -> Option<(i32, char)> {
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
