//! Setns mode: join the ns-holder's pre-opened namespaces, then spawn the tool.
//!
//! For each isolated-workspace call the runner `setns`es this single-threaded
//! caller into the holder's FDs in the order `user → mnt → pid → net`
//! (PID setns affects descendants only, so it precedes `fork`), optionally joins
//! the iws cgroup before spawning, then the child execs the command through the
//! same shell/tool primitive used by fresh-namespace mode. A
//! separate helper does the in-namespace overlay mount (`setns` into `user`+`mnt`,
//! then delegate to [`KernelMountPort`]).
//!
//! `setns(2)` is the only raw syscall here; child creation stays behind
//! [`std::process::Command`] in `fresh_ns::execute_tool`. `#![deny(unsafe_op_in_unsafe_fn)]`
//! still forces a `// SAFETY:` note on every FFI block.

#[cfg(target_os = "linux")]
use std::fs;
#[cfg(any(test, target_os = "linux"))]
use std::os::fd::RawFd;
#[cfg(any(test, target_os = "linux"))]
use std::path::PathBuf;
#[cfg(target_os = "linux")]
use std::time::Instant;

use crate::error::RunnerError;
use crate::mount::KernelMountPort;
#[cfg(target_os = "linux")]
use crate::mount::MountInputs;
#[cfg(any(test, target_os = "linux"))]
use crate::request::NsFds;
use crate::request::{RunRequest, RunResult};

/// `setns` into the held namespaces, then run the tool command.
///
/// # Safety
///
/// Calls `setns(2)` (which requires this to be the only thread in the process),
/// then delegates child spawning to the shared shell/tool primitive. The setns
/// FD order (`user`, `mnt`, `pid`, `net`) is load-bearing.
// PORT backend/src/sandbox/overlay/namespace_runner.py:138 — _run_tool_call_in_existing_namespace
// PORT backend/src/sandbox/isolated_workspace/scripts/setns_exec.py:34-94 — setns(order) → cgroup join → fork → execvp → waitpid
// PORT backend/src/sandbox/isolated_workspace/scripts/_setns_libc.py:18-25 — libc setns(2) wrapper
#[cfg(target_os = "linux")]
pub fn run_setns(
    request: &RunRequest,
    _mount: &dyn KernelMountPort,
) -> Result<RunResult, RunnerError> {
    // PORT backend/src/sandbox/isolated_workspace/scripts/setns_exec.py:54-94 —
    //   setns(user), setns(mnt), setns(pid), setns(net) in order; join cgroup.procs
    //   before fork; pipe stdin_b64 to the child; fork → execvp(argv); waitpid and
    //   map waitstatus → exit code. The group is its own session so cancel killpgs it.
    let ns_fds = require_ns_fds(request)?;
    join_cgroup(request);
    join_namespaces(&ns_fds)?;
    crate::fresh_ns::execute_tool(request, 0.0, setns_output_dir(request)?, Instant::now())
}

#[cfg(not(target_os = "linux"))]
pub fn run_setns(
    _request: &RunRequest,
    _mount: &dyn KernelMountPort,
) -> Result<RunResult, RunnerError> {
    Err(RunnerError::Unsupported)
}

/// Mount the overlay inside an existing workspace mount namespace: `setns` into
/// the holder's `user` then `mnt` FDs (granting `CAP_SYS_ADMIN` in that ns and
/// switching the mount table), then delegate to [`KernelMountPort`].
///
/// # Safety (future)
///
/// Calls `setns(2)` twice (`user`, then `mnt`) before the mount; must run on a
/// single-threaded caller until both setns calls complete.
// PORT backend/src/sandbox/isolated_workspace/scripts/setns_overlay_mount.py:43-86 — setns(user)→setns(mnt)→mount_overlay
#[cfg(target_os = "linux")]
pub fn setns_overlay_mount(
    request: &RunRequest,
    mount: &dyn KernelMountPort,
) -> Result<(), RunnerError> {
    // PORT backend/src/sandbox/isolated_workspace/scripts/setns_overlay_mount.py:54-86 —
    //   setns(ns_fds.user, CLONE_NEWUSER); setns(ns_fds.mnt, CLONE_NEWNS); then build
    //   MountInputs (newest-first lowerdirs + upper/work) and KernelMountPort::mount_overlay.
    let ns_fds = require_ns_fds(request)?;
    let user = ns_fds.user.ok_or_else(|| {
        RunnerError::InvalidRequest("setns overlay mount requires user ns fd".to_owned())
    })?;
    let mnt = ns_fds.mnt.ok_or_else(|| {
        RunnerError::InvalidRequest("setns overlay mount requires mnt ns fd".to_owned())
    })?;
    setns_fd("user", user.0, libc::CLONE_NEWUSER)?;
    setns_fd("mnt", mnt.0, libc::CLONE_NEWNS)?;
    let upperdir = request.upperdir.as_ref().ok_or_else(|| {
        RunnerError::InvalidRequest("setns overlay mount requires upperdir".to_owned())
    })?;
    let workdir = request.workdir.as_ref().ok_or_else(|| {
        RunnerError::InvalidRequest("setns overlay mount requires workdir".to_owned())
    })?;
    let guard = mount.mount_overlay(&MountInputs {
        workspace_root: request.workspace_root.0.clone(),
        layer_paths: overlay_layer_paths(request),
        upperdir: upperdir.clone(),
        workdir: workdir.clone(),
    })?;
    // The setns mount helper is a one-shot process. The mounted overlay must
    // outlive this helper and remain pinned by the target mount namespace until
    // isolated teardown, matching the Python helper that exits after mounting.
    std::mem::forget(guard);
    Ok(())
}

#[cfg(not(target_os = "linux"))]
pub fn setns_overlay_mount(
    _request: &RunRequest,
    _mount: &dyn KernelMountPort,
) -> Result<(), RunnerError> {
    Err(RunnerError::Unsupported)
}

#[cfg(any(test, target_os = "linux"))]
fn require_ns_fds(request: &RunRequest) -> Result<NsFds, RunnerError> {
    request
        .ns_fds
        .ok_or_else(|| RunnerError::InvalidRequest("setns mode requires ns_fds".to_owned()))
}

#[cfg(all(test, target_os = "linux"))]
fn namespace_fd_order(ns_fds: &NsFds) -> Vec<(&'static str, RawFd)> {
    namespace_fd_order_with_types(ns_fds)
        .into_iter()
        .map(|(name, fd, _)| (name, fd))
        .collect()
}

#[cfg(all(test, not(target_os = "linux")))]
fn namespace_fd_order(ns_fds: &NsFds) -> Vec<(&'static str, RawFd)> {
    [
        ("user", ns_fds.user),
        ("mnt", ns_fds.mnt),
        ("pid", ns_fds.pid),
        ("net", ns_fds.net),
    ]
    .into_iter()
    .filter_map(|(name, fd)| fd.map(|fd| (name, fd.0)))
    .collect()
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

#[cfg(any(test, target_os = "linux"))]
fn setns_output_dir(request: &RunRequest) -> Result<PathBuf, RunnerError> {
    request
        .upperdir
        .as_ref()
        .and_then(|upperdir| upperdir.parent().map(PathBuf::from))
        .ok_or_else(|| {
            RunnerError::InvalidRequest("setns mode requires upperdir with parent".to_owned())
        })
}

#[cfg(any(test, target_os = "linux"))]
fn overlay_layer_paths(request: &RunRequest) -> Vec<PathBuf> {
    if request.layer_paths.is_empty() {
        vec![request.workspace_root.0.clone()]
    } else {
        request.layer_paths.clone()
    }
}

#[cfg(target_os = "linux")]
fn join_cgroup(request: &RunRequest) {
    let Some(cgroup_path) = request.cgroup_path.as_ref() else {
        return;
    };
    let procs = cgroup_path.join("cgroup.procs");
    let _ = fs::write(procs, format!("{}\n", std::process::id()));
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
mod tests {
    use super::{namespace_fd_order, overlay_layer_paths, require_ns_fds, setns_output_dir};
    use crate::request::{Fd, NsFds, RunMode, RunRequest, ToolCall, WorkspaceRoot};
    use eos_protocol::Intent;
    use std::path::Path;

    #[test]
    fn require_ns_fds_rejects_missing_setns_payload() {
        let error = require_ns_fds(&request(None)).expect_err("ns_fds are required");
        assert!(error.to_string().contains("requires ns_fds"));
    }

    #[test]
    fn namespace_order_matches_python_helper_and_skips_missing_fds() {
        let ns_fds = NsFds {
            user: Some(Fd(10)),
            mnt: Some(Fd(11)),
            pid: None,
            net: Some(Fd(12)),
        };
        assert_eq!(
            namespace_fd_order(&ns_fds),
            vec![("user", 10), ("mnt", 11), ("net", 12)]
        );
    }

    #[test]
    fn setns_output_dir_is_parent_of_upperdir() {
        let request = RunRequest {
            upperdir: Some(Path::new("/tmp/iws/upper").to_path_buf()),
            ..request(Some(default_ns_fds()))
        };
        assert_eq!(
            setns_output_dir(&request).expect("upperdir has parent"),
            Path::new("/tmp/iws")
        );
    }

    #[test]
    fn overlay_layer_paths_fall_back_to_workspace_root() {
        let request = request(Some(default_ns_fds()));
        assert_eq!(
            overlay_layer_paths(&request),
            vec![Path::new("/testbed").to_path_buf()]
        );
    }

    fn request(ns_fds: Option<NsFds>) -> RunRequest {
        RunRequest {
            mode: RunMode::SetNs,
            tool_call: ToolCall {
                invocation_id: "test".to_owned(),
                agent_id: "agent".to_owned(),
                verb: "exec_command".to_owned(),
                intent: Intent::WriteAllowed,
                args: serde_json::json!({"command": "true"}),
                background: false,
            },
            workspace_root: WorkspaceRoot(Path::new("/testbed").to_path_buf()),
            layer_paths: vec![],
            upperdir: Some(Path::new("/tmp/iws/upper").to_path_buf()),
            workdir: Some(Path::new("/tmp/iws/work").to_path_buf()),
            ns_fds,
            cgroup_path: None,
            timeout_seconds: None,
        }
    }

    fn default_ns_fds() -> NsFds {
        NsFds {
            user: Some(Fd(10)),
            mnt: Some(Fd(11)),
            pid: Some(Fd(12)),
            net: Some(Fd(13)),
        }
    }
}
