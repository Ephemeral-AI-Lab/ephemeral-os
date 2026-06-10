//! Namespace runner: the syscalls the kernel forces into a single-threaded caller.
//!
//! The runner is the per-tool-call namespace child (`eosd ns-runner`). It relies
//! on the crate-level invariant — single-threaded, syscall-only, NO tokio —
//! because `unshare(CLONE_NEWUSER|…)` (fresh-ns mode) and `setns()` into a user
//! namespace (setns mode) both require the calling process/thread to be the only
//! thread in the process, or the syscall fails with `EINVAL`. Spawning this work
//! inline in the multithreaded tokio daemon would break it; instead the daemon
//! execs a dedicated single-threaded child whose body lives here.
//!
//! # Two modes
//!
//! 1. **Fresh-ns** ([`RunMode::FreshNs`]): `unshare(CLONE_NEWUSER|CLONE_NEWNS|…)` →
//!    write `uid_map`/`gid_map` → mount the overlay through
//!    [`eos_overlay::mount_overlay`] → spawn the tool → construct the result
//!    JSON → cleanup. One tool call per fresh namespace.
//! 2. **Setns** ([`RunMode::SetNs`]): per isolated call, `setns()` into the
//!    ns-holder's pre-opened namespace FDs (`user`, then `mnt`, then `pid`, then
//!    `net` — order is load-bearing) → `fork` → the child `execvp`s the command.
//!
//! # Process group / cancellation
//!
//! Both modes start the child in its own session/process group (via `setsid` /
//! `process_group(0)`) so the daemon can `killpg` the whole group from
//! outside — cancel kills the entire tree, not just the immediate child.
//!
//! # Build-time guarantee
//!
//! Linux-only syscall bodies are gated behind `#[cfg(target_os = "linux")]`; the
//! non-Linux arms return [`RunnerError::Unsupported`] so the workspace stays green
//! on the macOS dev host. Raw syscall sites carry focused `// SAFETY:` notes, and
//! `#![deny(unsafe_op_in_unsafe_fn)]` keeps that annotation discipline enforced.
//!
//! Internal deps: [`crate::protocol`] (the daemon↔runner wire DTOs [`RunRequest`] /
//! [`RunResult`] and the verb [`Intent`](crate::protocol::Intent)); `eos-overlay`
//! (kernel overlay mount and upper-dir capture primitives).

use crate::protocol::{RunMode, RunRequest, RunResult};

pub mod error;
mod fresh_ns;
#[cfg(target_os = "linux")]
mod mount_mask;
#[cfg(target_os = "linux")]
mod path;
pub mod setns;

pub mod config {
    pub use eos_config::configs::runner::*;
}

pub use error::RunnerError;

/// Execute one tool call through the runner, dispatching on [`RunRequest::mode`].
///
/// This is the runner's single entry point: the daemon hands a fully-resolved
/// [`RunRequest`] (already knowing whether it wants a fresh namespace or a setns
/// into an existing one) and the runner performs the syscalls on this
/// single-threaded caller.
///
/// Fresh-ns mode mounts the workspace overlay after `unshare`.
///
/// # Errors
///
/// Returns [`RunnerError`] when the request is invalid for the selected mode,
/// namespace setup fails, overlay mounting fails, or child execution fails.
pub fn run(request: &RunRequest, config: &config::RunnerConfig) -> Result<RunResult, RunnerError> {
    match request.mode {
        RunMode::FreshNs => fresh_ns::run_fresh_ns(request, config),
        RunMode::SetNs => setns::run_setns(request),
    }
}
