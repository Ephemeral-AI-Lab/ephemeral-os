//! The daemon↔ns-child wire protocol this crate executes.
//!
//! These DTOs model the JSON payloads exchanged with the `eosd ns-runner` /
//! `eosd ns-holder` children over stdin/stdout and the namespace
//! request/result files. The crate that runs the protocol owns the protocol:
//! callers (the daemon and the command-ops tier) build [`RunRequest`]s and
//! parse [`RunResult`]s by depending on this module — there is no shared
//! floor crate.

mod intent;
mod runner;

pub use intent::Intent;
pub use runner::{Fd, NsFds, RunMode, RunRequest, RunResult, RunnerVerb, ToolCall, WorkspaceRoot};
