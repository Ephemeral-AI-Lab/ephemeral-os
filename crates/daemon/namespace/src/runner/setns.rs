//! Setns mode: join holder namespaces, optionally mount overlay/DNS, run tool.

#[cfg(target_os = "linux")]
use std::ffi::CString;
#[cfg(target_os = "linux")]
use std::fs;
#[cfg(target_os = "linux")]
use std::os::fd::RawFd;
#[cfg(target_os = "linux")]
use std::os::unix::ffi::OsStrExt;
#[cfg(any(test, target_os = "linux"))]
use std::path::PathBuf;
#[cfg(target_os = "linux")]
use std::time::Instant;

#[cfg(target_os = "linux")]
use overlay::OverlayHandle;

use super::RunnerError;
#[cfg(any(test, target_os = "linux"))]
use crate::protocol::NsFds;
use crate::protocol::{RunRequest, RunResult};

#[cfg(target_os = "linux")]
const RESOLV_CONF: &str = "/etc/resolv.conf";

#[cfg(target_os = "linux")]
pub(crate) fn run_setns(request: &RunRequest) -> Result<RunResult, RunnerError> {
    let ns_fds = require_ns_fds(request)?;
    let mut timings = super::fresh_ns::RunnerPhaseTimings::default();
    let cgroup_start = Instant::now();
    join_cgroup(request)?;
    timings.insert_s(
        "workspace.cgroup_join_s",
        cgroup_start.elapsed().as_secs_f64(),
    );
    let setns_start = Instant::now();
    join_namespaces(&ns_fds)?;
    timings.insert_s(
        "workspace.setns_join_s",
        setns_start.elapsed().as_secs_f64(),
    );
    super::fresh_ns::execute_tool(request, timings, Instant::now(), None)
}

#[cfg(not(target_os = "linux"))]
pub(crate) fn run_setns(_request: &RunRequest) -> Result<RunResult, RunnerError> {
    Err(RunnerError::Unsupported)
}

/// Mount the overlay inside an existing workspace mount namespace.
#[cfg(target_os = "linux")]
pub fn setns_overlay_mount(
    request: &RunRequest,
    config: &super::config::RunnerConfig,
) -> Result<(), RunnerError> {
    setns_user_mnt(request, "setns overlay mount")?;
    let upperdir = request.upperdir.as_ref().ok_or_else(|| {
        RunnerError::InvalidRequest("setns overlay mount requires upperdir".to_owned())
    })?;
    let workdir = request.workdir.as_ref().ok_or_else(|| {
        RunnerError::InvalidRequest("setns overlay mount requires workdir".to_owned())
    })?;
    let handle = OverlayHandle {
        layer_paths: overlay_layer_paths(request),
        upperdir: upperdir.clone(),
        workdir: workdir.clone(),
    };
    let guard = overlay::mount_overlay(&request.workspace_root.0, &handle)?;
    super::mask_model_shell_paths(&config.mount_mask.hidden_paths)?;
    // The setns mount helper is a one-shot process. The mounted overlay must
    // outlive this helper and remain pinned by the target mount namespace until
    // isolated teardown, so the unmount-on-drop guard is deliberately leaked.
    std::mem::forget(guard);
    Ok(())
}

#[cfg(not(target_os = "linux"))]
pub fn setns_overlay_mount(
    _request: &RunRequest,
    _config: &super::config::RunnerConfig,
) -> Result<(), RunnerError> {
    Err(RunnerError::Unsupported)
}

/// Remount an overlay inside the runner's current mount namespace.
#[cfg(target_os = "linux")]
pub fn remount_overlay(request: &RunRequest) -> Result<(), RunnerError> {
    let upperdir = request.upperdir.as_ref().ok_or_else(|| {
        RunnerError::InvalidRequest("remount overlay requires upperdir".to_owned())
    })?;
    let workdir = request.workdir.as_ref().ok_or_else(|| {
        RunnerError::InvalidRequest("remount overlay requires workdir".to_owned())
    })?;
    if request.layer_paths.is_empty() {
        return Err(RunnerError::InvalidRequest(
            "remount overlay requires layer_paths".to_owned(),
        ));
    }
    let handle = OverlayHandle {
        upperdir: upperdir.clone(),
        workdir: workdir.clone(),
        layer_paths: request.layer_paths.clone(),
    };
    overlay::unmount_overlay(&request.workspace_root.0)?;
    let mount = match overlay::mount_overlay(&request.workspace_root.0, &handle) {
        Ok(mount) => mount,
        Err(err) if is_fsopen_unimplemented(&err) => {
            overlay::mount_overlay_legacy(&request.workspace_root.0, &handle)?
        }
        Err(err) => return Err(err.into()),
    };
    // The runner is a one-shot process; the refreshed overlay must outlive it.
    std::mem::forget(mount);
    Ok(())
}

#[cfg(not(target_os = "linux"))]
pub const fn remount_overlay(_request: &RunRequest) -> Result<(), RunnerError> {
    Err(RunnerError::Unsupported)
}

#[cfg(target_os = "linux")]
fn is_fsopen_unimplemented(err: &overlay::OverlayError) -> bool {
    const ENOSYS: i32 = 38;
    matches!(
        err,
        overlay::OverlayError::MountSyscall { context, source }
            if *context == "fsopen overlay" && source.raw_os_error() == Some(ENOSYS)
    )
}

/// Configure `/etc/resolv.conf` inside an existing workspace mount namespace.
#[cfg(target_os = "linux")]
pub fn configure_dns(request: &RunRequest) -> Result<serde_json::Value, RunnerError> {
    let fallback_dns = request
        .tool_call
        .args
        .get("fallback_dns")
        .and_then(serde_json::Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| {
            RunnerError::InvalidRequest("configure_dns requires fallback_dns".to_owned())
        })?;

    setns_user_mnt(request, "configure_dns")?;

    let content = match fs::read_to_string(RESOLV_CONF) {
        Ok(content) => content,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            return Ok(serde_json::json!({
                "applied_fallback": false,
                "previous_first_nameserver": null,
            }));
        }
        Err(err) => return Err(err.into()),
    };
    let previous = first_nameserver(&content).map(str::to_owned);
    let applied = previous.as_deref().is_some_and(needs_fallback_dns);
    if applied {
        bind_mount_resolv_conf(fallback_dns)?;
    }
    Ok(serde_json::json!({
        "applied_fallback": applied,
        "previous_first_nameserver": previous,
    }))
}

#[cfg(not(target_os = "linux"))]
pub const fn configure_dns(_request: &RunRequest) -> Result<serde_json::Value, RunnerError> {
    Err(RunnerError::Unsupported)
}

#[cfg(any(test, target_os = "linux"))]
fn require_ns_fds(request: &RunRequest) -> Result<NsFds, RunnerError> {
    request
        .ns_fds
        .ok_or_else(|| RunnerError::InvalidRequest("setns mode requires ns_fds".to_owned()))
}

#[cfg(target_os = "linux")]
fn namespace_fd_order_with_types(ns_fds: &NsFds) -> Vec<(&'static str, RawFd, libc::c_int)> {
    [
        ("user", ns_fds.user, libc::CLONE_NEWUSER),
        ("mnt", ns_fds.mnt, libc::CLONE_NEWNS),
        ("pid", ns_fds.pid, libc::CLONE_NEWPID),
        ("net", ns_fds.net, libc::CLONE_NEWNET),
    ]
    .into_iter()
    .filter_map(|(name, fd, nstype)| fd.map(|fd| (name, fd.0, nstype)))
    .collect()
}

#[cfg(target_os = "linux")]
fn setns_user_mnt(request: &RunRequest, operation: &str) -> Result<(), RunnerError> {
    let ns_fds = require_ns_fds(request)?;
    let user = ns_fds
        .user
        .ok_or_else(|| RunnerError::InvalidRequest(format!("{operation} requires user ns fd")))?;
    let mnt = ns_fds
        .mnt
        .ok_or_else(|| RunnerError::InvalidRequest(format!("{operation} requires mnt ns fd")))?;
    setns_fd("user", user.0, libc::CLONE_NEWUSER)?;
    setns_fd("mnt", mnt.0, libc::CLONE_NEWNS)
}

#[cfg(any(test, target_os = "linux"))]
fn overlay_layer_paths(request: &RunRequest) -> Vec<PathBuf> {
    if request.layer_paths.is_empty() {
        vec![request.workspace_root.0.clone()]
    } else {
        request.layer_paths.clone()
    }
}

#[cfg(any(test, target_os = "linux"))]
fn first_nameserver(content: &str) -> Option<&str> {
    content.lines().find_map(|line| {
        let stripped = line.trim();
        stripped
            .strip_prefix("nameserver")
            .and_then(|rest| rest.split_whitespace().next())
    })
}

#[cfg(any(test, target_os = "linux"))]
fn needs_fallback_dns(addr: &str) -> bool {
    addr.starts_with("127.")
}

#[cfg(target_os = "linux")]
fn bind_mount_resolv_conf(fallback_dns: &str) -> Result<(), RunnerError> {
    let path = std::env::temp_dir().join(format!(
        ".iws-resolv-{}-{}.conf",
        std::process::id(),
        unique_suffix()
    ));
    fs::write(&path, format!("nameserver {fallback_dns}\n"))?;
    let source = cstring_path(&path)?;
    let target = CString::new(RESOLV_CONF)
        .map_err(|err| RunnerError::InvalidRequest(format!("invalid resolv.conf path: {err}")))?;
    let fstype = CString::new("none")
        .map_err(|err| RunnerError::InvalidRequest(format!("invalid mount fstype: {err}")))?;
    // SAFETY: after `setns(user,mnt)` this helper has CAP_SYS_ADMIN in the
    // target namespace. The C strings live for the call; MS_BIND ignores data.
    let rc = unsafe {
        libc::mount(
            source.as_ptr(),
            target.as_ptr(),
            fstype.as_ptr(),
            libc::MS_BIND,
            std::ptr::null(),
        )
    };
    if rc == 0 {
        return Ok(());
    }
    Err(RunnerError::Syscall(std::io::Error::last_os_error()))
}

#[cfg(target_os = "linux")]
fn cstring_path(path: &std::path::Path) -> Result<CString, RunnerError> {
    CString::new(path.as_os_str().as_bytes()).map_err(|err| {
        RunnerError::InvalidRequest(format!(
            "path contains an interior nul byte: {} ({err})",
            path.display()
        ))
    })
}

#[cfg(target_os = "linux")]
fn unique_suffix() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos())
}

#[cfg(target_os = "linux")]
fn join_cgroup(request: &RunRequest) -> Result<(), RunnerError> {
    let Some(cgroup_path) = request.cgroup_path.as_ref() else {
        return Ok(());
    };
    let procs = cgroup_path.join("cgroup.procs");
    fs::write(procs, format!("{}\n", std::process::id())).map_err(RunnerError::Syscall)
}

#[cfg(target_os = "linux")]
fn join_namespaces(ns_fds: &NsFds) -> Result<(), RunnerError> {
    for (name, fd, nstype) in namespace_fd_order_with_types(ns_fds) {
        setns_fd(name, fd, nstype)?;
    }
    Ok(())
}

#[cfg(target_os = "linux")]
fn setns_fd(name: &str, fd: RawFd, nstype: libc::c_int) -> Result<(), RunnerError> {
    // SAFETY: `fd` is a borrowed namespace file descriptor supplied by the
    // daemon to this dedicated single-threaded runner process. `nstype` is the
    // matching CLONE_NEW* constant for that descriptor, and no Rust references
    // are invalidated by the kernel changing the current task's namespace.
    let rc = unsafe { libc::setns(fd, nstype) };
    if rc == 0 {
        return Ok(());
    }
    let err = std::io::Error::last_os_error();
    let kind = err.kind();
    Err(RunnerError::Syscall(std::io::Error::new(
        kind,
        format!("setns({name}, fd={fd}, nstype=0x{nstype:x}) failed: {err}"),
    )))
}

#[cfg(test)]
#[path = "../../tests/unit/runner/setns.rs"]
mod tests;
