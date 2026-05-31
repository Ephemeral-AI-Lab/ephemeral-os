//! Setns mode: join the ns-holder's pre-opened namespaces, then `fork` + `exec`.
//!
//! For each isolated-workspace call the runner `setns`es this single-threaded
//! caller into the holder's FDs in the order `user → mnt → pid → net`
//! (PID setns affects descendants only, so it precedes `fork`), optionally joins
//! the iws cgroup before `fork`, then the child `execvp`s the command. A
//! separate helper does the in-namespace overlay mount (`setns` into `user`+`mnt`,
//! then delegate to [`KernelMountPort`]).
//!
//! Future `unsafe`: the raw `setns(2)`, `fork(2)`, `execvp(3)` calls (Python uses
//! a `ctypes` libc wrapper because `os.setns` only exists in 3.12+). All bodies
//! are `todo!()` for now; `#![deny(unsafe_op_in_unsafe_fn)]` forces a `// SAFETY:`
//! note on every block when they are written.

use crate::error::RunnerError;
use crate::mount::KernelMountPort;
use crate::request::{RunRequest, RunResult};

/// `setns` into the held namespaces, then fork+exec the tool command.
///
/// # Safety (future)
///
/// Calls `setns(2)` (which requires this to be the only thread in the process),
/// then `fork(2)` and `execvp(3)`. The setns FD order (`user`, `mnt`, `pid`,
/// `net`) is load-bearing.
// PORT backend/src/sandbox/overlay/namespace_runner.py:138 — _run_tool_call_in_existing_namespace
// PORT backend/src/sandbox/isolated_workspace/scripts/setns_exec.py:34-94 — setns(order) → cgroup join → fork → execvp → waitpid
// PORT backend/src/sandbox/isolated_workspace/scripts/_setns_libc.py:18-25 — libc setns(2) wrapper
#[cfg(target_os = "linux")]
pub fn run_setns(
    _request: &RunRequest,
    _mount: &dyn KernelMountPort,
) -> Result<RunResult, RunnerError> {
    // PORT backend/src/sandbox/isolated_workspace/scripts/setns_exec.py:54-94 —
    //   setns(user), setns(mnt), setns(pid), setns(net) in order; join cgroup.procs
    //   before fork; pipe stdin_b64 to the child; fork → execvp(argv); waitpid and
    //   map waitstatus → exit code. The group is its own session so cancel killpgs it.
    todo!("PORT: setns(user/mnt/pid/net) → cgroup join → fork → execvp → waitpid")
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
    _request: &RunRequest,
    _mount: &dyn KernelMountPort,
) -> Result<(), RunnerError> {
    // PORT backend/src/sandbox/isolated_workspace/scripts/setns_overlay_mount.py:54-86 —
    //   setns(ns_fds.user, CLONE_NEWUSER); setns(ns_fds.mnt, CLONE_NEWNS); then build
    //   MountInputs (newest-first lowerdirs + upper/work) and KernelMountPort::mount_overlay.
    todo!("PORT: setns(user) → setns(mnt) → mount overlay in existing workspace mntns")
}

#[cfg(not(target_os = "linux"))]
pub fn setns_overlay_mount(
    _request: &RunRequest,
    _mount: &dyn KernelMountPort,
) -> Result<(), RunnerError> {
    Err(RunnerError::Unsupported)
}
