//! The command-session tool family and its lifecycle policy.
//!
//! [`CommandOps`] owns the `CommandId -> {PTY session, bound workspace}`
//! registry and decides what happens at settle: an ephemeral run **publishes**
//! its captured upperdir through the per-root single writer
//! (`eos_layerstack::service`), an isolated run **retains** its workspace
//! untouched, and a cancelled run **discards** — structurally, by enum arm,
//! never by flag.
//!
//! Lease custody lives here: an ephemeral run acquires its snapshot at start
//! and releases it at settle, however settle is reached (yield-wait, stdin
//! wait, poll, cancel, or the periodic sweep — first observer wins,
//! exactly-once via registry removal).
#![forbid(unsafe_code)]

mod binding;
#[cfg(target_os = "linux")]
mod ops;
mod outcome;
#[cfg(target_os = "linux")]
mod prepare;
mod registry;
#[cfg(target_os = "linux")]
mod settle;

pub use binding::CommandBinding;
#[cfg(target_os = "linux")]
pub use ops::{CommandOps, ExecTarget};
pub use outcome::{
    ChangedPathKinds, FinalizeCommandRequest, WorkspaceApiError, WorkspaceCommandOutcome,
    WorkspaceConflict, WorkspaceTimings,
};
