//! Launches `ns-runner` children for isolated namespace execution.
//!
//! This module owns the host-side process mechanics for `ns-runner`: request
//! encoding, child stdin/stdout wiring, detached service launch, and `nsenter`
//! remount helpers. The embedding binary must provide the `ns-runner`
//! subcommand on its current executable.

use std::fs::OpenOptions;
use std::io::Write;
#[cfg(unix)]
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use namespace::protocol::{RunRequest, RunResult};

/// Launches `ns-runner` children for overlay-backed runtime work.
///
/// The caller builds fully typed [`RunRequest`] values; the implementor owns
/// binary identity and child-process mechanics. This is intentionally limited
/// to the three launch shapes used by the runtime, not a generic process
/// abstraction.
pub trait NsRunnerLauncher: Send + Sync {
    /// Run one ns-runner request to completion.
    ///
    /// # Errors
    ///
    /// Returns a [`LaunchError`] when the request cannot be encoded, the child
    /// cannot be spawned/fed, exits unsuccessfully, or emits invalid output.
    fn run(&self, request: &RunRequest) -> Result<RunResult, LaunchError>;

    /// Spawn a long-lived ns-runner child.
    ///
    /// # Errors
    ///
    /// Returns a [`LaunchError`] when the request cannot be encoded or the
    /// child cannot be spawned/fed.
    fn spawn_detached(
        &self,
        request: &RunRequest,
        stderr_path: &Path,
    ) -> Result<Child, LaunchError>;

    /// Re-run a remount request inside an existing child's namespaces.
    ///
    /// # Errors
    ///
    /// Returns a [`LaunchError`] when the remount helper cannot be launched,
    /// times out, or exits unsuccessfully.
    fn remount_in(
        &self,
        target_pid: u32,
        request: &RunRequest,
        timeout: Duration,
    ) -> Result<(), LaunchError>;
}

/// Failures raised by an [`NsRunnerLauncher`]. Message text is preserved so
/// outer daemon/runtime error mapping can keep wire responses stable.
#[derive(Debug, thiserror::Error)]
pub enum LaunchError {
    /// The request could not be encoded or fed to the child.
    #[error("{0}")]
    InvalidRequest(String),

    /// A process or pipe I/O operation failed.
    #[error(transparent)]
    Io(#[from] std::io::Error),

    /// The launch pipeline failed.
    #[error("{0}")]
    Failed(String),
}

/// `current_exe` + `ns-runner` launcher used by the daemon binary.
#[derive(Debug, Default)]
pub struct CurrentExeNsRunnerLauncher;

impl NsRunnerLauncher for CurrentExeNsRunnerLauncher {
    fn run(&self, request: &RunRequest) -> Result<RunResult, LaunchError> {
        let payload = request_payload(request)?;
        let mut command = current_exe_ns_runner_command()?;
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = command.spawn()?;
        write_child_stdin(
            &mut child,
            &payload,
            LaunchError::Failed("ns-runner stdin unavailable".to_owned()),
        )?;
        let output = child.wait_with_output()?;
        if !output.status.success() {
            return Err(LaunchError::Failed(format!(
                "ns-runner exited with status {}: {}",
                output.status,
                String::from_utf8_lossy(&output.stderr)
            )));
        }
        serde_json::from_slice::<RunResult>(&output.stdout)
            .map_err(|err| LaunchError::Failed(format!("invalid ns-runner output: {err}")))
    }

    fn spawn_detached(
        &self,
        request: &RunRequest,
        stderr_path: &Path,
    ) -> Result<Child, LaunchError> {
        let payload = request_payload(request)?;
        let stderr = open_append(stderr_path)?;
        let mut command = current_exe_ns_runner_command()?;
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(stderr);
        let mut child = command.spawn()?;
        write_child_stdin(
            &mut child,
            &payload,
            LaunchError::InvalidRequest("ns-runner stdin unavailable".to_owned()),
        )?;
        drop(child.stdin.take());
        Ok(child)
    }

    fn remount_in(
        &self,
        target_pid: u32,
        request: &RunRequest,
        timeout: Duration,
    ) -> Result<(), LaunchError> {
        let payload = request_payload(request)?;
        let mut command = Command::new("nsenter");
        command
            .arg("-t")
            .arg(target_pid.to_string())
            .arg("-U")
            .arg("-m")
            .arg("--preserve-credentials")
            .arg("--")
            .arg(std::env::current_exe()?)
            .arg("ns-runner")
            .arg("--remount-overlay")
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::piped());
        start_new_process_group(&mut command);
        let mut child = command.spawn().map_err(|err| {
            LaunchError::Failed(format!(
                "failed to spawn nsenter for plugin service remount: {err}"
            ))
        })?;
        write_child_stdin(
            &mut child,
            &payload,
            LaunchError::Failed("nsenter stdin unavailable".to_owned()),
        )?;
        drop(child.stdin.take());
        let output = wait_for_helper(child, timeout, "plugin service remount")?;
        if output.status.success() {
            return Ok(());
        }
        Err(LaunchError::Failed(format!(
            "plugin service remount failed with status {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr).trim()
        )))
    }
}

fn open_append(path: &Path) -> Result<std::fs::File, LaunchError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(LaunchError::from)
}

fn request_payload(request: &RunRequest) -> Result<Vec<u8>, LaunchError> {
    serde_json::to_vec(request).map_err(|err| LaunchError::InvalidRequest(err.to_string()))
}

fn write_child_stdin(
    child: &mut Child,
    payload: &[u8],
    missing_stdin: LaunchError,
) -> Result<(), LaunchError> {
    child
        .stdin
        .as_mut()
        .ok_or(missing_stdin)?
        .write_all(payload)?;
    Ok(())
}

fn current_exe_ns_runner_command() -> Result<Command, LaunchError> {
    let mut command = Command::new(std::env::current_exe()?);
    command.arg("ns-runner");
    start_new_process_group(&mut command);
    Ok(command)
}

#[cfg(unix)]
fn start_new_process_group(command: &mut Command) {
    command.process_group(0);
}

#[cfg(not(unix))]
fn start_new_process_group(_command: &mut Command) {}

fn wait_for_helper(
    mut child: Child,
    timeout: Duration,
    label: &str,
) -> Result<std::process::Output, LaunchError> {
    let process_group_id = i32::try_from(child.id()).ok();
    let deadline = Instant::now() + timeout;
    loop {
        if child.try_wait()?.is_some() {
            return child.wait_with_output().map_err(LaunchError::from);
        }
        if Instant::now() >= deadline {
            terminate_process_group(process_group_id);
            let _ = child.kill();
            let output = child.wait_with_output()?;
            return Err(LaunchError::Failed(format!(
                "{label} timed out after {:.3}s: {}",
                timeout.as_secs_f64(),
                String::from_utf8_lossy(&output.stderr).trim()
            )));
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(unix)]
fn terminate_process_group(process_group_id: Option<i32>) {
    use nix::sys::signal::{killpg, Signal};
    use nix::unistd::Pid;

    let Some(pgid) = process_group_id else {
        return;
    };
    let pid = Pid::from_raw(pgid);
    if killpg(pid, Signal::SIGTERM).is_ok() {
        std::thread::sleep(Duration::from_millis(50));
    }
    let _ = killpg(pid, Signal::SIGKILL);
}

#[cfg(not(unix))]
fn terminate_process_group(_process_group_id: Option<i32>) {}
