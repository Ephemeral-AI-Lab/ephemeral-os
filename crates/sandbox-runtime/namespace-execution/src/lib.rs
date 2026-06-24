//! Daemon-side namespace execution engine — types and traits (Phase 1 skeleton).
//!
//! Workspace-agnostic: callers pass a `NamespaceTarget`, never a workspace type,
//! so this crate sits below `workspace` in the dependency graph.

mod error;
mod execution;
mod id;
mod observer;
mod promise;
mod registry;
mod shell;
mod target;

pub use error::NamespaceExecutionError;
pub use execution::{ExecutionHandle, InteractiveExecution};
pub use id::NamespaceExecutionId;
pub use observer::ExecutionObserver;
pub use shell::{RunnerOutcome, ShellOperation};
pub use target::NamespaceTarget;
