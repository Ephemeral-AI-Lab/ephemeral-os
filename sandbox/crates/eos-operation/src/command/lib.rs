#![forbid(unsafe_code)]

pub mod contract;

mod outcome;
mod prepare;
mod registry;
pub mod runtime;
mod service;
mod settle;

pub use contract::{
    CollectCompletedOutput, CommandCompletion, CommandMetadata, CommandResponse, CommandStatus,
};
pub use outcome::{ChangedPathKinds, WorkspaceConflict, WorkspaceTimings};
pub use runtime::{
    active_commands_for_caller, cancel_all_commands, cleanup_commands_for_caller, command_config,
    command_ops, command_scratch_root, configure_commands,
};
pub use service::{CommandOps, ExecTarget};
