//! Command-session PTY substrate.
//!
//! This crate owns the per-session process/PTY/transcript machinery: spawning
//! the runner, reaping the child into a policy-free [`ReapedCommand`], cancelling
//! the process group, and persisting the final response. The caller-keyed
//! workspace-run registry, the publish-vs-discard policy decision, and the
//! completion queue live in the daemon (`eos-daemon`'s `workspace_run` service),
//! which composes this substrate with the overlay/namespace workspace crates.

mod error;
pub mod output;
mod request;
mod response;
mod session;
#[cfg(target_os = "linux")]
mod transcript;
#[cfg(any(target_os = "linux", test))]
mod wait;

#[cfg(target_os = "linux")]
pub mod process;

pub mod config {
    pub use eos_config::configs::command_session::*;
}

pub use config::CommandSessionConfig;
pub use error::CommandSessionError;
pub use output::tail_lines;
pub use request::{
    CancelCommandSession, CollectCompleted, ReadCommandProgress, StartCommandSession, WriteStdin,
};
pub use response::{CollectCompletedResponse, CommandResponse, CommandSessionCompletion};
pub use session::{CommandSession, CommandSessionSpec};

#[cfg(target_os = "linux")]
pub use session::{ReapedCommand, RunningCommandSessionParts};
#[cfg(target_os = "linux")]
pub use wait::{wait_for_yield, CommandSessionWaitTarget, WaitOutcome};

pub type DynCommandWorkspacePolicy =
    Box<dyn eos_workspace_api::CommandWorkspacePolicy + Send + Sync + 'static>;
