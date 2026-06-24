//! Daemon-side namespace execution engine.
//!
//! Workspace-agnostic: callers pass a `NamespaceTarget`, never a workspace type,
//! so this crate sits below `workspace` in the dependency graph.
//!
//! `NamespaceExecutionEngine` drives both families over one dispatch flow:
//! reserve, spawn, mark running, wait, finalize, complete, resolve, and mark
//! terminal. The launcher, promise, and PTY substrate stay `pub(crate)`.

mod engine;
mod error;
mod execution;
mod id;
mod launcher;
mod observer;
mod promise;
mod pty;
mod registry;
mod shell;
mod status;
mod target;
mod transcript;

pub use engine::NamespaceExecutionEngine;
pub use error::NamespaceExecutionError;
pub use execution::{ExecutionHandle, InteractiveExecution};
pub use id::NamespaceExecutionId;
pub use launcher::{NsRunnerLauncher, RunnerChild};
pub use observer::{ExecutionObserver, NoopObserver};
pub use promise::{CompletionPromise, CompletionWaiter};
pub use pty::{open_pty_pair, PtyMaster};
pub use registry::ExecutionRegistry;
pub use shell::{RunnerOutcome, ShellOperation};
pub use status::NamespaceExecutionTerminalStatus;
pub use target::NamespaceTarget;
