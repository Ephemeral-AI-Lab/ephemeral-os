//! Command process PTY substrate.
//!
//! This crate owns the per-command process/PTY/transcript machinery: spawning
//! the runner child, taking its exit into a policy-free [`process::CommandProcessExit`],
//! cancelling the process group, yield-waiting on output, and persisting the
//! final response. It carries no workspace policy: who runs on which workspace,
//! and what happens to the upperdir at finalization, is the command-ops tier's
//! concern. The mode string a final response carries is opaque here.
//!
//! Mechanism crate, like `eos-overlay` and `eos-namespace`. The sandbox
//! runtime this crate backs only ever runs on Linux, so the crate compiles
//! for Linux alone; type-check from other hosts via
//! `cargo check --target x86_64-unknown-linux-gnu`.
#![forbid(unsafe_code)]

mod contract;
pub mod process;
mod pty;
mod transcript;
pub mod yield_wait_loop;

pub use contract::{
    tail_lines, CancelCommand, CollectCompleted, CommandError, ReadCommandProgress, StartCommand,
    WriteStdin,
};
pub use eos_config::configs::command::CommandConfig;
pub use process::{CommandProcess, CommandProcessSpec};
