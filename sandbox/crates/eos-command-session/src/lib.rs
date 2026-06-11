//! Command-session PTY substrate.
//!
//! This crate owns the per-session process/PTY/transcript machinery: spawning
//! the runner child, reaping it into a policy-free [`session::ReapedCommand`],
//! cancelling the process group, yield-waiting on output, and persisting the
//! final response. It carries **no workspace policy**: who runs on which
//! workspace, and what happens to the upperdir at settle, is the command-ops
//! tier's concern. The mode string a settled response carries is opaque here.
//!
//! Mechanism crate, like `eos-overlay` and `eos-namespace`. The sandbox
//! runtime this crate backs only ever runs on Linux, so the crate compiles
//! for Linux alone; type-check from other hosts via
//! `cargo check --target x86_64-unknown-linux-gnu`.
#![forbid(unsafe_code)]

mod contract;
pub mod process;
pub mod session;
mod transcript;
pub mod yield_wait_loop;

pub use contract::{
    CancelCommandSession, CollectCompleted, CollectCompletedResponse, CommandResponse,
    CommandSessionCompletion, CommandSessionError, ReadCommandProgress, StartCommandSession,
    WriteStdin,
};
pub use eos_config::configs::command_session::CommandSessionConfig;
pub use session::{CommandSession, CommandSessionSpec};
