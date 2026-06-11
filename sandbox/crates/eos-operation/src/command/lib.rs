#![forbid(unsafe_code)]

pub mod contract;

mod outcome;
mod prepare;
mod registry;
pub mod runtime;
mod service;
mod settle;

pub use contract::{
    CollectCompletedResponse, CommandMetadata, CommandResponse, CommandSessionCompletion,
    CommandStatus,
};
pub use outcome::{ChangedPathKinds, WorkspaceConflict, WorkspaceTimings};
pub use runtime::{
    active_command_sessions_for_caller, cancel_all_command_sessions,
    cleanup_command_sessions_for_caller, command_ops, command_session_config,
    command_session_scratch_root, configure_command_sessions,
};
pub use service::{CommandOps, ExecTarget};
