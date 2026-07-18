use std::env;
use std::fs::File;
use std::io;
use std::io::{Read, Write};
use std::os::fd::{AsRawFd, OwnedFd, RawFd};
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use nix::sys::signal::{kill, Signal};
use nix::unistd::Pid;
use rustix::fs::{fcntl_getfl, fcntl_setfl, OFlags};
use rustix::io::{fcntl_setfd, FdFlags};
use rustix::pipe::pipe;
use sandbox_runtime_namespace_process::runner::protocol::{NamespaceRunnerRequest, RunResult};

use crate::error::NamespaceExecutionError;
use crate::pty::{open_pty_pair, terminate_pgid, PtyMaster};

pub(crate) const SHELL_MODE_FLAG: &str = "--shell";
pub(crate) const MOUNT_OVERLAY_MODE_FLAG: &str = "--mount-overlay";
pub(crate) const FILE_OP_MODE_FLAG: &str = "--file-op";
pub(crate) const REMOUNT_OVERLAY_MODE_FLAG: &str = "--remount-overlay";
const SETUP_WAIT_POLL: Duration = Duration::from_millis(1);

/// Shared launch placement policy: the optional workspace `cgroup.procs` path the
/// launcher writes the freshly spawned ns-runner pid into. This is not file-op
/// logic — `exec_command` and session file ops pass a cgroup; overlay mount
/// passes none.
#[derive(Debug, Clone)]
pub struct RunnerPlacement {
    pub cgroup_procs_path: Option<PathBuf>,
}

impl RunnerPlacement {
    #[must_use]
    pub fn none() -> Self {
        Self {
            cgroup_procs_path: None,
        }
    }
}

pub trait NsRunnerLauncher: Send + Sync {
    fn spawn_pty(
        &self,
        request: NamespaceRunnerRequest,
        transcript_path: Option<PathBuf>,
        cancelled: Arc<AtomicBool>,
        placement: RunnerPlacement,
    ) -> Result<(Box<dyn RunnerChild>, PtyMaster), NamespaceExecutionError>;

    fn spawn_overlay_mount(
        &self,
        request: NamespaceRunnerRequest,
        placement: RunnerPlacement,
        setup_timeout_s: f64,
    ) -> Result<Box<dyn RunnerChild>, NamespaceExecutionError>;

    fn spawn_file_op(
        &self,
        request: NamespaceRunnerRequest,
        placement: RunnerPlacement,
        setup_timeout_s: f64,
    ) -> Result<Box<dyn RunnerChild>, NamespaceExecutionError>;

    /// Spawn the staged-switch remount runner. Defaulted so existing
    /// launcher fakes keep compiling; the production launcher overrides.
    fn spawn_remount_overlay(
        &self,
        request: NamespaceRunnerRequest,
        placement: RunnerPlacement,
        setup_timeout_s: f64,
    ) -> Result<Box<dyn RunnerChild>, NamespaceExecutionError> {
        let _ = (request, placement, setup_timeout_s);
        Err(NamespaceExecutionError::Spawn(
            "this launcher does not support the remount-overlay runner".to_owned(),
        ))
    }
}

pub trait RunnerChild: Send {
    fn wait_completion(&mut self) -> Result<RunResult, NamespaceExecutionError>;

    fn try_wait_completion(&mut self) -> Result<Option<RunResult>, NamespaceExecutionError> {
        self.wait_completion().map(Some)
    }

    fn terminate(&mut self) {}
}

pub(crate) struct ForkRunnerLauncher {
    stdin_write_deadline: Duration,
    max_result_bytes: usize,
}

impl ForkRunnerLauncher {
    pub(crate) fn new(caps: crate::caps::ExecutionCaps) -> Self {
        Self {
            stdin_write_deadline: caps.stdin_write_deadline,
            max_result_bytes: caps.max_runner_result_bytes,
        }
    }
}

struct ForkRunnerChild {
    child: Child,
    result_read: File,
    mode_flag: Option<&'static str>,
    setup_timeout_s: f64,
    max_result_bytes: usize,
    started_at: Instant,
    result_bytes: Vec<u8>,
    result_over_cap: bool,
    result_eof: bool,
    exit_status: Option<ExitStatus>,
    timed_out: bool,
    shutdown: bool,
    terminate_sent_at: Option<Instant>,
    kill_sent: bool,
}

struct SpawnedRunner {
    child: Child,
    result_read: OwnedFd,
    request_write: OwnedFd,
    pgid: i32,
}

static SPAWN_CRITICAL_SECTION: Mutex<()> = Mutex::new(());

impl NsRunnerLauncher for ForkRunnerLauncher {
    fn spawn_pty(
        &self,
        request: NamespaceRunnerRequest,
        transcript_path: Option<PathBuf>,
        cancelled: Arc<AtomicBool>,
        placement: RunnerPlacement,
    ) -> Result<(Box<dyn RunnerChild>, PtyMaster), NamespaceExecutionError> {
        let request_bytes = encode_request(&request)?;
        let (mut spawned, master) = spawn_locked(Some(SHELL_MODE_FLAG), |command| {
            let (master, slave) = open_pty_pair().map_err(spawn_error)?;
            command
                .stdin(Stdio::from(slave.try_clone().map_err(spawn_error)?))
                .stdout(Stdio::from(slave.try_clone().map_err(spawn_error)?))
                .stderr(Stdio::from(slave));
            install_pgid_leader_hook(command);
            Ok(master)
        })?;
        place_spawned_child_in_cgroup(&mut spawned, placement.cgroup_procs_path.as_deref())?;
        let pgid = spawned.pgid;
        let cancel: Box<dyn Fn() + Send + Sync> = Box::new(move || {
            cancelled.store(true, Ordering::Release);
            terminate_pgid(pgid);
        });
        let pty = PtyMaster::spawn(
            master,
            Some(pgid),
            transcript_path,
            cancel,
            self.stdin_write_deadline,
        )
        .map_err(spawn_error);
        let pty = match pty {
            Ok(pty) => pty,
            Err(error) => {
                terminate_spawned_child(&mut spawned.child, Some(pgid));
                return Err(error);
            }
        };
        let child = spawned.into_child(&request_bytes, None, 0.0, self.max_result_bytes)?;
        Ok((Box::new(child), pty))
    }

    fn spawn_overlay_mount(
        &self,
        request: NamespaceRunnerRequest,
        placement: RunnerPlacement,
        setup_timeout_s: f64,
    ) -> Result<Box<dyn RunnerChild>, NamespaceExecutionError> {
        spawn_request_result(
            MOUNT_OVERLAY_MODE_FLAG,
            request,
            placement,
            setup_timeout_s,
            self.max_result_bytes,
        )
    }

    fn spawn_file_op(
        &self,
        request: NamespaceRunnerRequest,
        placement: RunnerPlacement,
        setup_timeout_s: f64,
    ) -> Result<Box<dyn RunnerChild>, NamespaceExecutionError> {
        spawn_request_result(
            FILE_OP_MODE_FLAG,
            request,
            placement,
            setup_timeout_s,
            self.max_result_bytes,
        )
    }

    fn spawn_remount_overlay(
        &self,
        request: NamespaceRunnerRequest,
        placement: RunnerPlacement,
        setup_timeout_s: f64,
    ) -> Result<Box<dyn RunnerChild>, NamespaceExecutionError> {
        spawn_request_result(
            REMOUNT_OVERLAY_MODE_FLAG,
            request,
            placement,
            setup_timeout_s,
            self.max_result_bytes,
        )
    }
}

/// Shared launch for the non-interactive request/result runner modes
/// (`--mount-overlay`, `--file-op`): null stdio, cgroup placement, and the
/// setup-timeout wait that drains `result_fd` concurrently.
fn spawn_request_result(
    mode_flag: &'static str,
    request: NamespaceRunnerRequest,
    placement: RunnerPlacement,
    setup_timeout_s: f64,
    max_result_bytes: usize,
) -> Result<Box<dyn RunnerChild>, NamespaceExecutionError> {
    let request_bytes = encode_request(&request)?;
    let (mut spawned, ()) = spawn_locked(Some(mode_flag), |command| {
        command
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        install_pgid_leader_hook(command);
        Ok(())
    })?;
    place_spawned_child_in_cgroup(&mut spawned, placement.cgroup_procs_path.as_deref())?;
    Ok(Box::new(spawned.into_child(
        &request_bytes,
        Some(mode_flag),
        setup_timeout_s,
        max_result_bytes,
    )?))
}

impl SpawnedRunner {
    fn into_child(
        self,
        request_bytes: &[u8],
        mode_flag: Option<&'static str>,
        setup_timeout_s: f64,
        max_result_bytes: usize,
    ) -> Result<ForkRunnerChild, NamespaceExecutionError> {
        let SpawnedRunner {
            mut child,
            result_read,
            request_write,
            pgid,
        } = self;
        if let Err(error) = write_request(request_write, request_bytes) {
            terminate_spawned_child(&mut child, Some(pgid));
            return Err(error);
        }
        ForkRunnerChild::new(
            child,
            result_read,
            mode_flag,
            setup_timeout_s,
            max_result_bytes,
        )
    }
}

impl ForkRunnerChild {
    fn new(
        child: Child,
        result_read: OwnedFd,
        mode_flag: Option<&'static str>,
        setup_timeout_s: f64,
        max_result_bytes: usize,
    ) -> Result<Self, NamespaceExecutionError> {
        let result_read = File::from(result_read);
        set_nonblocking(&result_read).map_err(spawn_error)?;
        Ok(Self {
            child,
            result_read,
            mode_flag,
            setup_timeout_s,
            max_result_bytes,
            started_at: Instant::now(),
            result_bytes: Vec::new(),
            result_over_cap: false,
            result_eof: false,
            exit_status: None,
            timed_out: false,
            shutdown: false,
            terminate_sent_at: None,
            kill_sent: false,
        })
    }
}

fn spawn_locked<R>(
    mode_flag: Option<&'static str>,
    configure: impl FnOnce(&mut Command) -> Result<R, NamespaceExecutionError>,
) -> Result<(SpawnedRunner, R), NamespaceExecutionError> {
    let _spawn_guard = spawn_lock();
    let (request_read, request_write) = request_pipe()?;
    let (result_read, result_write) = result_pipe()?;
    let mut command = ns_runner_command(
        mode_flag,
        request_read.as_raw_fd(),
        result_write.as_raw_fd(),
    )?;
    let resource = configure(&mut command)?;
    let mut child = command.spawn().map_err(spawn_error)?;
    drop(request_read);
    drop(result_write);
    let pgid = match child_pgid(&child) {
        Ok(pgid) => pgid,
        Err(error) => {
            terminate_spawned_child(&mut child, None);
            return Err(error);
        }
    };
    Ok((
        SpawnedRunner {
            child,
            result_read,
            request_write,
            pgid,
        },
        resource,
    ))
}

impl RunnerChild for ForkRunnerChild {
    fn wait_completion(&mut self) -> Result<RunResult, NamespaceExecutionError> {
        loop {
            if let Some(result) = self.try_wait_completion()? {
                return Ok(result);
            }
            thread::sleep(SETUP_WAIT_POLL);
        }
    }

    fn try_wait_completion(&mut self) -> Result<Option<RunResult>, NamespaceExecutionError> {
        self.drain_available_result()?;
        if self.exit_status.is_none() {
            self.exit_status = self.child.try_wait().map_err(spawn_error)?;
        }
        if self.exit_status.is_none() {
            self.enforce_termination_deadline();
            return Ok(None);
        }

        self.drain_available_result()?;
        if !self.result_eof {
            return Ok(None);
        }
        if self.shutdown {
            return Err(NamespaceExecutionError::Spawn(
                "namespace runner terminated during supervisor shutdown".to_owned(),
            ));
        }
        if self.timed_out {
            return Err(timeout_error(
                self.mode_flag.unwrap_or("namespace execution"),
            ));
        }
        if self.result_over_cap {
            return Err(NamespaceExecutionError::Spawn(format!(
                "ns-runner result exceeds {} bytes",
                self.max_result_bytes
            )));
        }
        if let Ok(result) = serde_json::from_slice::<RunResult>(&self.result_bytes) {
            return Ok(Some(result));
        }
        let status = self
            .exit_status
            .take()
            .expect("completion requires a reaped child status");
        synthesize_result(status).map(Some)
    }

    fn terminate(&mut self) {
        if self.exit_status.is_some() || self.shutdown {
            return;
        }
        self.shutdown = true;
        terminate_child(&mut self.child, Signal::SIGKILL);
        self.kill_sent = true;
    }
}

impl ForkRunnerChild {
    fn drain_available_result(&mut self) -> Result<(), NamespaceExecutionError> {
        if self.result_eof {
            return Ok(());
        }
        let mut chunk = [0_u8; 64 * 1024];
        loop {
            match self.result_read.read(&mut chunk) {
                Ok(0) => {
                    self.result_eof = true;
                    return Ok(());
                }
                Ok(read) => {
                    if !self.result_over_cap
                        && self.result_bytes.len().saturating_add(read) > self.max_result_bytes
                    {
                        self.result_over_cap = true;
                        self.result_bytes.clear();
                    }
                    if !self.result_over_cap {
                        self.result_bytes.extend_from_slice(&chunk[..read]);
                    }
                }
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => return Ok(()),
                Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
                Err(error) => return Err(spawn_error(error)),
            }
        }
    }

    fn enforce_termination_deadline(&mut self) {
        let now = Instant::now();
        if !self.timed_out
            && self.mode_flag.is_some()
            && now.duration_since(self.started_at) >= setup_timeout_duration(self.setup_timeout_s)
        {
            self.timed_out = true;
            self.terminate_sent_at = Some(now);
            terminate_child(&mut self.child, Signal::SIGTERM);
        }
        if self.timed_out
            && !self.kill_sent
            && self
                .terminate_sent_at
                .is_some_and(|sent_at| now.duration_since(sent_at) >= Duration::from_millis(100))
        {
            terminate_child(&mut self.child, Signal::SIGKILL);
            self.kill_sent = true;
        }
    }
}

fn ns_runner_command(
    mode_flag: Option<&str>,
    request_fd: RawFd,
    result_fd: RawFd,
) -> Result<Command, NamespaceExecutionError> {
    let mut command = Command::new(env::current_exe().map_err(spawn_error)?);
    command.arg("ns-runner");
    if let Some(mode_flag) = mode_flag {
        command.arg(mode_flag);
    }
    command
        .arg("--request-fd")
        .arg(request_fd.to_string())
        .arg("--result-fd")
        .arg(result_fd.to_string());
    Ok(command)
}

fn write_request(request: OwnedFd, request_bytes: &[u8]) -> Result<(), NamespaceExecutionError> {
    File::from(request)
        .write_all(request_bytes)
        .map_err(spawn_error)?;
    Ok(())
}

fn child_pgid(child: &Child) -> Result<i32, NamespaceExecutionError> {
    i32::try_from(child.id()).map_err(|_| {
        NamespaceExecutionError::Spawn(format!("child pid does not fit i32: {}", child.id()))
    })
}

/// Fail-closed placement of a freshly spawned `ns-runner` into the configured
/// workspace cgroup. Membership inherits across runner re-exec/fork/setns. If
/// placement fails, terminate and wait the just-spawned process group before
/// returning, so no command can escape the configured workload limits.
fn place_spawned_child_in_cgroup(
    spawned: &mut SpawnedRunner,
    cgroup_procs_path: Option<&Path>,
) -> Result<(), NamespaceExecutionError> {
    let pid = spawned.child.id();
    if let Some(path) = cgroup_procs_path {
        if let Err(error) = std::fs::write(path, pid.to_string()) {
            terminate_spawned_child(&mut spawned.child, Some(spawned.pgid));
            return Err(NamespaceExecutionError::Spawn(format!(
                "place ns-runner pid {pid} in {}: {error}",
                path.display()
            )));
        }
    }
    Ok(())
}

fn install_pgid_leader_hook(command: &mut Command) {
    // SAFETY: `pre_exec` runs in the forked child immediately before `exec`.
    // The closure only calls async-signal-safe `setpgid(2)` and returns the
    // OS error if it fails; it does not touch shared Rust state.
    unsafe {
        command.pre_exec(|| {
            if libc::setpgid(0, 0) == 0 {
                Ok(())
            } else {
                Err(io::Error::last_os_error())
            }
        });
    }
}

fn spawn_lock() -> std::sync::MutexGuard<'static, ()> {
    SPAWN_CRITICAL_SECTION
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn synthesize_result(status: ExitStatus) -> Result<RunResult, NamespaceExecutionError> {
    let exit_code = status
        .code()
        .or_else(|| status.signal().map(|signal| -signal))
        .unwrap_or(1);
    if exit_code == 0 {
        return Err(NamespaceExecutionError::Completion(
            "runner exited successfully without a valid result envelope".to_owned(),
        ));
    }
    Ok(RunResult {
        exit_code,
        payload: serde_json::json!({ "status": "error" }),
    })
}

fn setup_timeout_duration(setup_timeout_s: f64) -> Duration {
    let seconds = if setup_timeout_s.is_finite() {
        setup_timeout_s.max(0.0)
    } else {
        0.0
    };
    Duration::from_secs_f64(seconds)
}

fn terminate_child(child: &mut Child, signal: Signal) {
    let Ok(pid) = i32::try_from(child.id()) else {
        if signal == Signal::SIGKILL {
            let _ = child.kill();
        }
        return;
    };
    let _ = kill(Pid::from_raw(-pid), signal);
    let _ = kill(Pid::from_raw(pid), signal);
}

fn terminate_spawned_child(child: &mut Child, pgid: Option<i32>) {
    if let Some(pgid) = pgid {
        terminate_pgid(pgid);
    } else {
        terminate_child(child, Signal::SIGKILL);
    }
    let _ = child.wait();
}

fn timeout_error(mode_flag: &str) -> NamespaceExecutionError {
    NamespaceExecutionError::Spawn(format!("ns-runner {mode_flag} timed out"))
}

fn set_nonblocking(file: &File) -> io::Result<()> {
    let flags = fcntl_getfl(file)?;
    fcntl_setfl(file, flags | OFlags::NONBLOCK)?;
    Ok(())
}

fn encode_request(request: &NamespaceRunnerRequest) -> Result<Vec<u8>, NamespaceExecutionError> {
    serde_json::to_vec(request).map_err(|error| {
        NamespaceExecutionError::Spawn(format!("serialize runner request: {error}"))
    })
}

fn request_pipe() -> Result<(OwnedFd, OwnedFd), NamespaceExecutionError> {
    let (read, write) = pipe().map_err(spawn_error)?;
    fcntl_setfd(&read, FdFlags::empty()).map_err(spawn_error)?;
    fcntl_setfd(&write, FdFlags::CLOEXEC).map_err(spawn_error)?;
    Ok((read, write))
}

fn result_pipe() -> Result<(OwnedFd, OwnedFd), NamespaceExecutionError> {
    let (read, write) = pipe().map_err(spawn_error)?;
    fcntl_setfd(&read, FdFlags::CLOEXEC).map_err(spawn_error)?;
    fcntl_setfd(&write, FdFlags::empty()).map_err(spawn_error)?;
    Ok((read, write))
}

fn spawn_error(error: impl std::fmt::Display) -> NamespaceExecutionError {
    NamespaceExecutionError::Spawn(error.to_string())
}
