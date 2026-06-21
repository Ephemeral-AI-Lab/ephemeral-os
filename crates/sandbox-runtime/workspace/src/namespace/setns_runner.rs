#[cfg(target_os = "linux")]
use std::io::Read;
#[cfg(target_os = "linux")]
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
#[cfg(target_os = "linux")]
use std::process::{Child, Command, ExitStatus, Output, Stdio};
#[cfg(target_os = "linux")]
use std::thread;
#[cfg(target_os = "linux")]
use std::time::{Duration, Instant};

#[cfg(target_os = "linux")]
use ::sandbox_runtime_namespace_process::runner::protocol::{NamespaceCommandRequest, RunResult};
#[cfg(target_os = "linux")]
use nix::sys::signal::{kill, Signal};
#[cfg(target_os = "linux")]
use nix::unistd::Pid;
#[cfg(target_os = "linux")]
use serde_json::json;

#[cfg(target_os = "linux")]
use crate::isolated_setup::{BRIDGE_PREFIX_LEN, GATEWAY};
use crate::lifecycle::remount::{RemountOverlayReport, RemountProbe};
use crate::profile::IsolatedNetworkError;
use crate::profile::WorkspaceModeHandle;

#[cfg(target_os = "linux")]
use super::fds::{expect_line, ns_fds_from_mode, write_all_fd};
#[cfg(target_os = "linux")]
use super::holder::ns_holder_runtime_error;
#[cfg(target_os = "linux")]
use super::setup_error;
use super::NamespaceRuntime;

impl NamespaceRuntime {
    pub(crate) fn mount_overlay(
        &self,
        handle: &WorkspaceModeHandle,
        layer_paths: &[PathBuf],
        setup_timeout_s: f64,
    ) -> Result<(), IsolatedNetworkError> {
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (handle, layer_paths, setup_timeout_s);
        }
        #[cfg(target_os = "linux")]
        {
            let request = ns_command_request(handle, "mount", json!({}), layer_paths.to_vec());
            mount_overlay_child(&request, setup_timeout_s)?;
        }
        Ok(())
    }

    pub(crate) fn remount_overlay(
        &self,
        handle: &WorkspaceModeHandle,
        layer_paths: &[PathBuf],
        probe: &RemountProbe,
        setup_timeout_s: f64,
    ) -> Result<RemountOverlayReport, IsolatedNetworkError> {
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (handle, layer_paths, probe, setup_timeout_s);
            Ok(RemountOverlayReport::default())
        }
        #[cfg(target_os = "linux")]
        {
            let request = ns_command_request(
                handle,
                "remount",
                json!({
                    "probe_path": probe
                        .path
                        .as_ref()
                        .map(|path| path.to_string_lossy().into_owned()),
                    "probe_content": probe.expected_content.as_deref(),
                }),
                layer_paths.to_vec(),
            );
            remount_overlay_child(&request, setup_timeout_s)
        }
    }

    pub(crate) fn signal_net_ready(
        &self,
        handle: &WorkspaceModeHandle,
        setup_timeout_s: f64,
    ) -> Result<(), IsolatedNetworkError> {
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (handle, setup_timeout_s);
        }
        #[cfg(target_os = "linux")]
        {
            let payload = handle.veth.as_ref().map_or_else(
                || "net-ready\n".to_owned(),
                |veth| {
                    format!(
                        "net-ready {} {} {} {}\n",
                        veth.ns_name, veth.ns_ip, BRIDGE_PREFIX_LEN, GATEWAY
                    )
                },
            );
            write_all_fd(handle.control_fd, payload.as_bytes())?;
            if let Err(error) = expect_line(handle.readiness_fd, b"ready", setup_timeout_s) {
                return Err(ns_holder_runtime_error(error, handle.holder_pid)?);
            }
        }
        Ok(())
    }
}

#[cfg(target_os = "linux")]
pub(crate) fn ns_command_request(
    handle: &WorkspaceModeHandle,
    request: &str,
    args: serde_json::Value,
    layer_paths: Vec<PathBuf>,
) -> NamespaceCommandRequest {
    NamespaceCommandRequest {
        request_id: format!("isolated-{request}-{}", handle.workspace_id.0),
        args,
        workspace_root: PathBuf::from(&handle.workspace_root),
        layer_paths,
        upperdir: Some(handle.dirs.upperdir.clone()),
        workdir: Some(handle.dirs.workdir.clone()),
        ns_fds: ns_fds_from_mode(handle.ns_fds),
        cgroup_path: handle.cgroup_path.clone(),
        timeout_seconds: None,
    }
}

#[cfg(target_os = "linux")]
pub(super) fn mount_overlay_child(
    request: &NamespaceCommandRequest,
    setup_timeout_s: f64,
) -> Result<(), IsolatedNetworkError> {
    let output = run_child(request, "--mount-overlay", setup_timeout_s)?;
    if output.status.success() {
        return Ok(());
    }
    Err(IsolatedNetworkError::SetupFailed {
        step: format!(
            "ns-runner mount overlay failed with status {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
        ),
    })
}

#[cfg(target_os = "linux")]
pub(super) fn remount_overlay_child(
    request: &NamespaceCommandRequest,
    setup_timeout_s: f64,
) -> Result<RemountOverlayReport, IsolatedNetworkError> {
    let output = run_child(request, "--remount-overlay", setup_timeout_s)?;
    if output.status.success() {
        let result = serde_json::from_slice::<RunResult>(&output.stdout).map_err(|err| {
            IsolatedNetworkError::SetupFailed {
                step: format!("invalid ns-runner remount overlay output: {err}"),
            }
        })?;
        return Ok(RemountOverlayReport::from_payload(&result.payload));
    }
    Err(IsolatedNetworkError::SetupFailed {
        step: format!(
            "ns-runner remount overlay failed with status {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
        ),
    })
}

#[cfg(target_os = "linux")]
pub(crate) fn run_child(
    request: &NamespaceCommandRequest,
    mode_arg: &str,
    setup_timeout_s: f64,
) -> Result<Output, IsolatedNetworkError> {
    let payload = serde_json::to_vec(request).map_err(setup_error)?;
    let paths = RunnerPayloadPaths::new(mode_arg);
    std::fs::write(&paths.request_path, payload).map_err(setup_error)?;
    let mut child = Command::new(std::env::current_exe().map_err(setup_error)?)
        .arg("ns-runner")
        .arg(mode_arg)
        .arg("--request")
        .arg(&paths.request_path)
        .arg("--output")
        .arg(&paths.output_path)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .map_err(setup_error)?;
    let status = wait_for_child(&mut child, mode_arg, setup_timeout_s)?;
    let stdout = std::fs::read(&paths.output_path).unwrap_or_default();
    let stderr = read_pipe(child.stderr.take())?;
    paths.cleanup();
    Ok(Output {
        status,
        stdout,
        stderr,
    })
}

#[cfg(target_os = "linux")]
struct RunnerPayloadPaths {
    request_path: PathBuf,
    output_path: PathBuf,
}

#[cfg(target_os = "linux")]
impl RunnerPayloadPaths {
    fn new(mode_arg: &str) -> Self {
        let suffix = mode_arg.trim_start_matches('-');
        let base = std::env::temp_dir().join(format!(
            "eos-ns-runner-{suffix}-{}-{}",
            std::process::id(),
            unique_suffix()
        ));
        Self {
            request_path: base.with_extension("request.json"),
            output_path: base.with_extension("result.json"),
        }
    }

    fn cleanup(&self) {
        let _ = std::fs::remove_file(&self.request_path);
        let _ = std::fs::remove_file(&self.output_path);
    }
}

#[cfg(target_os = "linux")]
fn unique_suffix() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos())
}

#[cfg(target_os = "linux")]
fn wait_for_child(
    child: &mut Child,
    mode_arg: &str,
    setup_timeout_s: f64,
) -> Result<ExitStatus, IsolatedNetworkError> {
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
                    return Err(IsolatedNetworkError::SetupFailed {
                        step: format!("ns-runner {mode_arg} timed out"),
                    });
                }
                thread::sleep(Duration::from_millis(10));
            }
            terminate_child(child, Signal::SIGKILL);
            let _ = child.wait();
            return Err(IsolatedNetworkError::SetupFailed {
                step: format!("ns-runner {mode_arg} timed out"),
            });
        }
        thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(target_os = "linux")]
fn terminate_child(child: &mut Child, signal: Signal) {
    let Ok(pid) = i32::try_from(child.id()) else {
        let _ = child.kill();
        return;
    };
    let _ = kill(Pid::from_raw(-pid), signal);
    let _ = kill(Pid::from_raw(pid), signal);
}

#[cfg(target_os = "linux")]
fn read_pipe<R: Read>(pipe: Option<R>) -> Result<Vec<u8>, IsolatedNetworkError> {
    let Some(mut pipe) = pipe else {
        return Ok(Vec::new());
    };
    let mut bytes = Vec::new();
    pipe.read_to_end(&mut bytes).map_err(setup_error)?;
    Ok(bytes)
}
