use std::io::{Read, Write};
use std::os::unix::process::CommandExt;
use std::process::{Child, Command, ExitStatus, Output, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use ::namespace::protocol::{RunRequest, RunResult};
use nix::sys::signal::{kill, Signal};
use nix::unistd::Pid;
use serde_json::Value;

use crate::isolated_workspace::error::IsolatedError;
use crate::isolated_workspace::manager::DnsConfiguration;
use crate::isolated_workspace::RemountOverlayReport;

use super::setup_error;

pub(super) fn mount_overlay_child(
    request: &RunRequest,
    setup_timeout_s: f64,
) -> Result<(), IsolatedError> {
    let output = run_child(request, "--mount-overlay", Stdio::null(), setup_timeout_s)?;
    if output.status.success() {
        return Ok(());
    }
    Err(IsolatedError::SetupFailed {
        step: format!(
            "ns-runner mount overlay failed with status {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
        ),
    })
}

pub(super) fn remount_overlay_child(
    request: &RunRequest,
    setup_timeout_s: f64,
) -> Result<RemountOverlayReport, IsolatedError> {
    let output = run_child(
        request,
        "--remount-overlay",
        Stdio::piped(),
        setup_timeout_s,
    )?;
    if output.status.success() {
        let result = serde_json::from_slice::<RunResult>(&output.stdout).map_err(|err| {
            IsolatedError::SetupFailed {
                step: format!("invalid ns-runner remount overlay output: {err}"),
            }
        })?;
        return Ok(RemountOverlayReport::from_payload(&result.payload));
    }
    Err(IsolatedError::SetupFailed {
        step: format!(
            "ns-runner remount overlay failed with status {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
        ),
    })
}

pub(super) fn configure_dns_child(
    request: &RunRequest,
    setup_timeout_s: f64,
) -> Result<DnsConfiguration, IsolatedError> {
    let output = run_child(request, "--configure-dns", Stdio::piped(), setup_timeout_s)?;
    if !output.status.success() {
        return Err(IsolatedError::SetupFailed {
            step: format!(
                "ns-runner configure dns failed with status {}: {}",
                output.status,
                String::from_utf8_lossy(&output.stderr)
            ),
        });
    }
    let result = serde_json::from_slice::<RunResult>(&output.stdout).map_err(|err| {
        IsolatedError::SetupFailed {
            step: format!("invalid ns-runner configure dns output: {err}"),
        }
    })?;
    Ok(DnsConfiguration {
        fallback_applied: result
            .payload
            .get("applied_fallback")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        previous_first_nameserver: result
            .payload
            .get("previous_first_nameserver")
            .and_then(Value::as_str)
            .map(str::to_owned),
    })
}

fn run_child(
    request: &RunRequest,
    mode_arg: &str,
    stdout: Stdio,
    setup_timeout_s: f64,
) -> Result<Output, IsolatedError> {
    let payload = serde_json::to_vec(request).map_err(setup_error)?;
    let mut child = Command::new(std::env::current_exe().map_err(setup_error)?)
        .arg("ns-runner")
        .arg(mode_arg)
        .stdin(Stdio::piped())
        .stdout(stdout)
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .map_err(setup_error)?;
    child
        .stdin
        .as_mut()
        .ok_or_else(|| IsolatedError::SetupFailed {
            step: "ns-runner stdin unavailable".to_owned(),
        })?
        .write_all(&payload)
        .map_err(setup_error)?;
    drop(child.stdin.take());
    let status = wait_for_child(&mut child, mode_arg, setup_timeout_s)?;
    let stdout = read_pipe(child.stdout.take())?;
    let stderr = read_pipe(child.stderr.take())?;
    Ok(Output {
        status,
        stdout,
        stderr,
    })
}

fn wait_for_child(
    child: &mut Child,
    mode_arg: &str,
    setup_timeout_s: f64,
) -> Result<ExitStatus, IsolatedError> {
    let deadline = Instant::now() + Duration::from_secs_f64(setup_timeout_s.max(0.0));
    loop {
        if let Some(status) = child.try_wait().map_err(setup_error)? {
            return Ok(status);
        }
        if Instant::now() >= deadline {
            terminate_child(child, Signal::SIGTERM);
            let grace_deadline = Instant::now() + Duration::from_millis(100);
            while Instant::now() < grace_deadline {
                if let Some(status) = child.try_wait().map_err(setup_error)? {
                    let _ = status;
                    return Err(IsolatedError::SetupFailed {
                        step: format!("ns-runner {mode_arg} timed out"),
                    });
                }
                thread::sleep(Duration::from_millis(10));
            }
            terminate_child(child, Signal::SIGKILL);
            let _ = child.wait();
            return Err(IsolatedError::SetupFailed {
                step: format!("ns-runner {mode_arg} timed out"),
            });
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn terminate_child(child: &mut Child, signal: Signal) {
    let Ok(pid) = i32::try_from(child.id()) else {
        let _ = child.kill();
        return;
    };
    let _ = kill(Pid::from_raw(-pid), signal);
    let _ = kill(Pid::from_raw(pid), signal);
}

fn read_pipe<R: Read>(pipe: Option<R>) -> Result<Vec<u8>, IsolatedError> {
    let Some(mut pipe) = pipe else {
        return Ok(Vec::new());
    };
    let mut bytes = Vec::new();
    pipe.read_to_end(&mut bytes).map_err(setup_error)?;
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ns_runner_wait_times_out_and_reaps_child_group() -> Result<(), Box<dyn std::error::Error>> {
        let mut child = Command::new("sh")
            .arg("-c")
            .arg("sleep 60")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0)
            .spawn()?;

        let error = wait_for_child(&mut child, "--test-timeout", 0.01)
            .expect_err("sleeping child should time out");

        assert!(error.to_string().contains("timed out"));
        assert!(
            child.try_wait()?.is_some(),
            "timed out child should be reaped"
        );
        Ok(())
    }
}
