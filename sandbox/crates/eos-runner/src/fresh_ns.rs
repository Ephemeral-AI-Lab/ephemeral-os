//! Fresh-namespace mode: `unshare` → `uid_map` → mount overlay → `execve`.
//!
//! This is the daemon's standard per-tool-call path. The Python target spawns
//! `unshare -Urm python -m sandbox.overlay.namespace_entrypoint <payload>` with
//! `start_new_session=True`; the Rust port does the `unshare(CLONE_NEWUSER|
//! CLONE_NEWNS)` itself in this single-threaded child, writes the uid/gid maps,
//! delegates the overlay mount to [`KernelMountPort`], then `execve`s the tool —
//! all in one process group so cancel can `killpg` the whole tree.
//!
//! Future `unsafe`: the `unshare`/`execve`/`setsid` raw syscalls. None exist yet
//! — the bodies are `todo!()`. When implemented each block carries a `// SAFETY:`
//! note and the crate's `#![deny(unsafe_op_in_unsafe_fn)]` forces it.

use crate::error::RunnerError;
use crate::mount::KernelMountPort;
use crate::request::{RunRequest, RunResult};

/// Run one tool call in a freshly-unshared namespace.
///
/// # Safety (future)
///
/// Will call `unshare(2)`, `execve(2)`, and `setsid(2)`. These require the
/// process to be single-threaded (the crate-level invariant) and the caller to
/// own the namespace it creates.
// PORT backend/src/sandbox/overlay/namespace_runner.py:72 — _run_tool_call_in_fresh_namespace
// PORT backend/src/sandbox/overlay/namespace_runner.py:227-250 — _run_namespace_entrypoint_async (unshare -Urm, start_new_session=True)
// PORT backend/src/sandbox/overlay/namespace_entrypoint.py:92-135 — mount_and_execute_tool_payload (mount overlay then exec)
#[cfg(target_os = "linux")]
pub fn run_fresh_ns(
    _request: &RunRequest,
    _mount: &dyn KernelMountPort,
) -> Result<RunResult, RunnerError> {
    // PORT backend/src/sandbox/overlay/namespace_runner.py:72-135 — full fresh-ns
    //   sequence: unshare(CLONE_NEWUSER|CLONE_NEWNS) on this single-threaded child,
    //   write /proc/self/{uid_map,setgroups,gid_map}, KernelMountPort::mount_overlay
    //   at workspace_root, setsid + execve the tool, then read the result JSON and
    //   reap the process group on cancel/timeout.
    todo!("PORT: fresh-namespace unshare → uid_map → mount overlay → execve → result JSON")
}

#[cfg(not(target_os = "linux"))]
pub fn run_fresh_ns(
    _request: &RunRequest,
    _mount: &dyn KernelMountPort,
) -> Result<RunResult, RunnerError> {
    Err(RunnerError::Unsupported)
}
