#![forbid(unsafe_code)]

pub mod contract;

mod finalize;
mod outcome;
mod prepare;
mod registry;
pub mod runtime;
mod service;
mod trace;

pub use contract::{
    CollectCompletedOutput, CommandCompletion, CommandMetadata, CommandResponse, CommandStatus,
};
pub use outcome::{ChangedPathKinds, WorkspaceConflict, WorkspaceTimings};
pub use runtime::{
    active_commands_for_caller, cancel_all_commands, cleanup_commands_for_caller, command_config,
    command_ops, command_scratch_root, configure_commands,
};
pub use service::{
    CommandExecError, CommandExecOutcome, CommandOps, CommandProgressTraceFacts,
    CommandReadProgressOutcome, CommandStdinTraceFacts, CommandWriteStdinOutcome, ExecTarget,
};
pub use trace::CommandTraceEvent;
