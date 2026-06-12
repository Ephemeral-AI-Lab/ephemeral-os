//! Fresh-namespace mode: unshare, mount overlay, spawn the tool.

#[cfg(target_os = "linux")]
use std::ffi::OsStr;
#[cfg(target_os = "linux")]
use std::fs;
#[cfg(target_os = "linux")]
use std::os::unix::process::CommandExt;
#[cfg(target_os = "linux")]
use std::path::{Path, PathBuf};
#[cfg(target_os = "linux")]
use std::process::{Command, Stdio};
#[cfg(target_os = "linux")]
use std::time::Instant;

#[cfg(target_os = "linux")]
use rustix::io::Errno;
#[cfg(target_os = "linux")]
use rustix::mount::{mount_change, MountPropagationFlags};
#[cfg(target_os = "linux")]
use rustix::process::{kill_process_group, setsid, Pid, Signal};
#[cfg(target_os = "linux")]
use rustix::thread::{set_thread_gid, set_thread_uid, unshare, UnshareFlags};

#[cfg(target_os = "linux")]
use eos_overlay::OverlayHandle;

use super::RunnerError;
#[cfg(target_os = "linux")]
use crate::protocol::RunnerVerb;
use crate::protocol::{RunRequest, RunResult};
#[cfg(target_os = "linux")]
use serde_json::{json, Value};

#[cfg(target_os = "linux")]
mod child;
#[cfg(target_os = "linux")]
mod command;

#[cfg(target_os = "linux")]
use child::*;
#[cfg(target_os = "linux")]
use command::*;

#[cfg(target_os = "linux")]
pub(crate) fn run_fresh_ns(
    request: &RunRequest,
    config: &super::config::RunnerConfig,
) -> Result<RunResult, RunnerError> {
    enter_fresh_namespace()?;
    let upperdir = request
        .upperdir
        .as_ref()
        .ok_or_else(|| RunnerError::InvalidRequest("fresh-ns requires upperdir".to_owned()))?;
    let workdir = request
        .workdir
        .as_ref()
        .ok_or_else(|| RunnerError::InvalidRequest("fresh-ns requires workdir".to_owned()))?;
    let mount_start = Instant::now();
    let handle = OverlayHandle {
        layer_paths: request.layer_paths.clone(),
        upperdir: upperdir.clone(),
        workdir: workdir.clone(),
    };
    let mount_guard = eos_overlay::mount_overlay(&request.workspace_root.0, &handle)?;
    let mount_s = mount_start.elapsed().as_secs_f64();

    let mut result = execute_tool(
        request,
        mount_s,
        Instant::now(),
        Some(&config.mount_mask.hidden_paths),
    )?;
    record_overlay_teardown(&mut result, mount_guard, request.layer_paths.len());
    Ok(result)
}

#[cfg(not(target_os = "linux"))]
pub(crate) fn run_fresh_ns(
    _request: &RunRequest,
    _config: &super::config::RunnerConfig,
) -> Result<RunResult, RunnerError> {
    Err(RunnerError::Unsupported)
}

#[cfg(target_os = "linux")]
fn enter_fresh_namespace() -> Result<(), RunnerError> {
    let parent_uid = rustix::process::getuid().as_raw();
    let parent_gid = rustix::process::getgid().as_raw();

    if let Err(err) = setsid() {
        // Docker exec may launch the runner as a process-group leader. In that
        // case setsid(2) returns EPERM, but the spawned tool below still gets
        // its own process group for timeout/cancel cleanup.
        if err != Errno::PERM {
            return Err(RunnerError::Syscall(std::io::Error::from(err)));
        }
    }
    unshare(UnshareFlags::NEWUSER | UnshareFlags::NEWNS).map_syscall()?;
    write_if_exists("/proc/self/setgroups", "deny\n")?;
    fs::write("/proc/self/uid_map", format!("0 {parent_uid} 1\n")).map_err(RunnerError::Syscall)?;
    fs::write("/proc/self/gid_map", format!("0 {parent_gid} 1\n")).map_err(RunnerError::Syscall)?;
    set_thread_gid(rustix::process::Gid::ROOT).map_syscall()?;
    set_thread_uid(rustix::process::Uid::ROOT).map_syscall()?;
    mount_change(
        "/",
        MountPropagationFlags::PRIVATE | MountPropagationFlags::REC,
    )
    .map_syscall()?;
    Ok(())
}

#[cfg(target_os = "linux")]
pub(crate) fn execute_tool(
    request: &RunRequest,
    mount_s: f64,
    run_start: Instant,
    hidden_paths: Option<&[PathBuf]>,
) -> Result<RunResult, RunnerError> {
    match &request.tool_call.verb {
        RunnerVerb::ExecCommand => execute_shell(request, mount_s, run_start, hidden_paths),
        RunnerVerb::PluginService => execute_plugin_service(request, mount_s, run_start),
        RunnerVerb::Unknown(verb) => Ok(error_result(
            2,
            "unsupported_runner_verb",
            &format!("fresh namespace runner does not support verb {}", verb),
        )),
    }
}

#[cfg(target_os = "linux")]
fn execute_plugin_service(
    request: &RunRequest,
    mount_s: f64,
    run_start: Instant,
) -> Result<RunResult, RunnerError> {
    let argv = plugin_service_argv(request)?;
    let cwd = shell_cwd(request)?;
    let mut command = Command::new(&argv[0]);
    command
        .args(&argv[1..])
        .current_dir(cwd)
        .envs(command_environment(&request.tool_call.args))
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0);

    let mut child = command.spawn().map_err(RunnerError::Child)?;
    let child_pid = Pid::from_child(&child);
    let (exit_code, timed_out) = match wait_for_child(&mut child, request.timeout_seconds) {
        Ok(exit_code) => (exit_code, false),
        Err(RunnerError::TimedOut) => (124, true),
        Err(err) => return Err(err),
    };
    if timed_out || !matches!(request.mode, crate::protocol::RunMode::SetNs) {
        let _ = kill_process_group(child_pid, Signal::Kill);
    }
    Ok(RunResult {
        exit_code,
        payload: serde_json::json!({
            "success": exit_code == 0,
            "workspace": "ephemeral",
            "timings": {
                "workspace.mount_s": mount_s,
                "workspace.tool_s": run_start.elapsed().as_secs_f64(),
            },
            "status": result_status(exit_code, timed_out),
        }),
    })
}

#[cfg(target_os = "linux")]
fn execute_shell(
    request: &RunRequest,
    mount_s: f64,
    run_start: Instant,
    hidden_paths: Option<&[PathBuf]>,
) -> Result<RunResult, RunnerError> {
    let argv = shell_argv(request)?;
    let cwd = shell_cwd(request)?;
    // Open a handle to the real /proc before the mount mask hides it, so the
    // scope-wait can still enumerate same-pgid background processes even though
    // the model shell sees an empty masked /proc.
    let proc_dir = rustix::fs::open(
        "/proc",
        rustix::fs::OFlags::RDONLY | rustix::fs::OFlags::DIRECTORY | rustix::fs::OFlags::CLOEXEC,
        rustix::fs::Mode::empty(),
    )
    .ok();
    if let Some(hidden_paths) = hidden_paths {
        super::mask_model_shell_paths(hidden_paths)?;
    }
    let mut command = Command::new(&argv[0]);
    command
        .args(&argv[1..])
        .current_dir(cwd)
        .env_clear()
        .envs(command_environment(&request.tool_call.args))
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());

    let mut child = command.spawn().map_err(RunnerError::Child)?;
    let (exit_code, timed_out) = match wait_for_command_execution_scope(
        &mut child,
        request.timeout_seconds,
        proc_dir.as_ref().map(std::os::fd::AsFd::as_fd),
    ) {
        Ok(exit_code) => (exit_code, false),
        Err(RunnerError::TimedOut) => (124, true),
        Err(err) => return Err(err),
    };
    Ok(RunResult {
        exit_code,
        payload: serde_json::json!({
            "success": exit_code == 0,
            "workspace": "ephemeral",
            "timings": {
                "workspace.mount_s": mount_s,
                "workspace.tool_s": run_start.elapsed().as_secs_f64(),
            },
            "conflict": null,
            "conflict_reason": null,
            "changed_paths": [],
            "error": null,
            "changed_path_kinds": {},
            "mutation_source": "",
            "status": result_status(exit_code, timed_out),
            "exit_code": exit_code,
            "stdout": "",
            "stderr": "",
            "warnings": [],
        }),
    })
}

#[cfg(target_os = "linux")]
const fn result_status(exit_code: i32, timed_out: bool) -> &'static str {
    if timed_out {
        "timed_out"
    } else if exit_code == 0 {
        "ok"
    } else {
        "error"
    }
}

#[cfg(target_os = "linux")]
fn write_if_exists(path: impl AsRef<Path>, value: impl AsRef<OsStr>) -> Result<(), RunnerError> {
    match fs::write(path.as_ref(), value.as_ref().as_encoded_bytes()) {
        Ok(()) => Ok(()),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(err) => Err(RunnerError::Syscall(err)),
    }
}

#[cfg(target_os = "linux")]
fn error_result(exit_code: i32, kind: &str, message: &str) -> RunResult {
    RunResult {
        exit_code,
        payload: serde_json::json!({
            "success": false,
            "workspace": "ephemeral",
            "status": "error",
            "error": {
                "kind": kind,
                "message": message,
            },
            "timings": {},
        }),
    }
}

#[cfg(target_os = "linux")]
fn record_overlay_teardown(
    result: &mut RunResult,
    mount_guard: eos_overlay::OverlayMount,
    layer_count: usize,
) {
    let unmount_start = Instant::now();
    let unmount_result = mount_guard.unmount();
    let unmount_s = unmount_start.elapsed().as_secs_f64();
    let fsconfig_calls = layer_count.saturating_add(3);

    let Some(payload) = result.payload.as_object_mut() else {
        return;
    };
    let timings = payload.entry("timings").or_insert_with(|| json!({}));
    if let Some(timings) = timings.as_object_mut() {
        timings.insert("workspace.unmount_s".to_owned(), json!(unmount_s));
        timings.insert("workspace.layer_count".to_owned(), json!(layer_count));
        timings.insert("workspace.fsconfig_calls".to_owned(), json!(fsconfig_calls));
    }
    match unmount_result {
        Ok(()) => {
            payload.insert("workspace_unmount_error".to_owned(), Value::Null);
        }
        Err(err) => {
            let message = err.to_string();
            payload.insert("workspace_unmount_error".to_owned(), json!(message));
            let warnings = payload.entry("warnings").or_insert_with(|| json!([]));
            if let Some(warnings) = warnings.as_array_mut() {
                warnings.push(json!({
                    "kind": "workspace_unmount_failed",
                    "message": message,
                }));
            }
        }
    }
}

#[cfg(target_os = "linux")]
trait SyscallResult<T> {
    fn map_syscall(self) -> Result<T, RunnerError>;
}

#[cfg(target_os = "linux")]
impl<T> SyscallResult<T> for rustix::io::Result<T> {
    fn map_syscall(self) -> Result<T, RunnerError> {
        self.map_err(|err| RunnerError::Syscall(std::io::Error::from(err)))
    }
}
