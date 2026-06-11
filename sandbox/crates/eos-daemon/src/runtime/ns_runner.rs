//! The ns-runner launch seam and the daemon's implementation of it.
//!
//! The "current binary has an `ns-runner` subcommand" contract belongs to the
//! `eosd` binary, so the plugin runtime never assumes it: it receives a
//! [`NsRunnerLauncher`] and the daemon implements the three launch shapes here
//! (oneshot run, detached service spawn, in-namespace remount), including the
//! invocation-registry process-group bookkeeping for cancellable runs.

use std::io::Write;
use std::os::unix::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::time::{Duration, Instant};

use eos_namespace::protocol::{RunRequest, RunResult};
use eos_runtime::{LaunchError, NsRunnerLauncher};

use crate::invocation_registry::{terminate_process_group, InFlightRegistry};

/// The daemon's launcher: `current_exe` + the `ns-runner` subcommand, `nsenter`
/// for remounts, and process-group registration against the in-flight
/// invocation registry when one is attached.
#[derive(Default)]
pub(crate) struct DaemonNsRunnerLauncher {
    /// Registry for cancellable process-group bookkeeping. The server does not
    /// currently attach its registry here (matching the pre-seam behavior
    /// where every caller passed `None`).
    invocation_registry: Option<Arc<InFlightRegistry>>,
}

impl NsRunnerLauncher for DaemonNsRunnerLauncher {
    fn run(&self, request: &RunRequest) -> Result<RunResult, LaunchError> {
        let payload = serde_json::to_vec(request)
            .map_err(|err| LaunchError::InvalidRequest(err.to_string()))?;
        let mut command = Command::new(std::env::current_exe()?);
        command
            .arg("ns-runner")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        command.process_group(0);
        let mut child = command.spawn()?;
        if let Some(registry) = &self.invocation_registry {
            if let Ok(pgid) = i32::try_from(child.id()) {
                registry.register_process_group(&request.tool_call.invocation_id, pgid);
            }
        }
        child
            .stdin
            .as_mut()
            .ok_or_else(|| LaunchError::Failed("ns-runner stdin unavailable".to_owned()))?
            .write_all(&payload)?;
        let output = child.wait_with_output()?;
        if let Some(registry) = &self.invocation_registry {
            registry.clear_process_group(&request.tool_call.invocation_id);
        }
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

    fn spawn_detached(&self, request: &RunRequest) -> Result<Child, LaunchError> {
        let payload = serde_json::to_vec(request)
            .map_err(|err| LaunchError::InvalidRequest(err.to_string()))?;
        let mut command = Command::new(std::env::current_exe()?);
        command
            .arg("ns-runner")
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        command.process_group(0);
        let mut child = command.spawn()?;
        child
            .stdin
            .as_mut()
            .ok_or_else(|| LaunchError::InvalidRequest("ns-runner stdin unavailable".to_owned()))?
            .write_all(&payload)?;
        drop(child.stdin.take());
        Ok(child)
    }

    fn remount_in(
        &self,
        target_pid: u32,
        request: &RunRequest,
        timeout: Duration,
    ) -> Result<(), LaunchError> {
        let payload = serde_json::to_vec(request)
            .map_err(|err| LaunchError::InvalidRequest(err.to_string()))?;
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
        command.process_group(0);
        let mut child = command.spawn().map_err(|err| {
            LaunchError::Failed(format!(
                "failed to spawn nsenter for plugin service remount: {err}"
            ))
        })?;
        child
            .stdin
            .as_mut()
            .ok_or_else(|| LaunchError::Failed("nsenter stdin unavailable".to_owned()))?
            .write_all(&payload)?;
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
