//! Daemon-side namespace execution engine (Phase 2).
//!
//! Workspace-agnostic: callers pass a `NamespaceTarget`, never a workspace type,
//! so this crate sits below `workspace` in the dependency graph.
//!
//! `NamespaceExecutionEngine` drives both families over one Template-Method
//! dispatch (reserve → spawn → `on_running` → watcher{ wait → finalize →
//! `complete` → `resolve` → `on_terminal` }) against a `pub(crate)` launcher
//! Bridge seam. The seam, registry, promise, and PTY substrate stay `pub(crate)`;
//! they are exercised through this crate's `tests/` suites via the
//! `test-support`-gated `test_support` facade (fakes included).

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

#[cfg(feature = "test-support")]
mod fakes;

pub use engine::NamespaceExecutionEngine;
pub use error::NamespaceExecutionError;
pub use execution::{ExecutionHandle, InteractiveExecution};
pub use id::NamespaceExecutionId;
pub use observer::ExecutionObserver;
pub use registry::{CompletedExecution, ExecutionRegistry};
pub use shell::{RunnerOutcome, ShellOperation};
pub use status::NamespaceExecutionTerminalStatus;
pub use target::NamespaceTarget;

/// Internal seam surfaced to this crate's `tests/` suites — the `pub(crate)`
/// launcher Bridge, promise, PTY substrate, and the fakes that drive them.
/// Available only under the `test-support` feature.
#[cfg(feature = "test-support")]
pub mod test_support {
    pub use crate::fakes::{
        outcome, run_result, run_result_without_status, sample_target, ErrShellOp, FakeLauncher,
        FakeObserver, ObserverEvent, OkShellOp,
    };
    pub use crate::launcher::{NsRunnerLauncher, RunnerChild};
    pub use crate::promise::CompletionPromise;
    pub use crate::pty::{open_pty_pair, PtyMaster};
}
