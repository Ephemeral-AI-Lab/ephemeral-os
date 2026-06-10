//! Command-session PTY substrate.
//!
//! This crate owns the per-session process/PTY/transcript machinery: spawning
//! the runner child, reaping it into a policy-free [`session::ReapedCommand`],
//! cancelling the process group, yield-waiting on output, and persisting the
//! final response. It carries **no workspace policy**: who runs on which
//! workspace, and what happens to the upperdir at settle, is the command-ops
//! tier's concern. The mode string a settled response carries is opaque here.
//!
//! Mechanism crate, like `eos-overlay` and `eos-namespace`: the PTY surface is
//! Linux-only; the request/response DTOs, the session scaffold, and the
//! yield-wait loop compile everywhere so non-Linux hosts can type-check and
//! unit-test the policy tiers above.
#![forbid(unsafe_code)]

mod error;
#[cfg(target_os = "linux")]
pub mod process;
mod request;
mod response;
pub mod session;
pub(crate) mod tail;
#[cfg(target_os = "linux")]
mod transcript;
pub mod wait;

pub use eos_config::configs::command_session::CommandSessionConfig;
pub use error::CommandSessionError;
pub use request::{
    CancelCommandSession, CollectCompleted, ReadCommandProgress, StartCommandSession, WriteStdin,
};
pub use response::{CollectCompletedResponse, CommandResponse, CommandSessionCompletion};
pub use session::{CommandSession, CommandSessionSpec};
