#![forbid(unsafe_code)]

#[cfg(target_os = "linux")]
mod ops;
mod outcome;
#[cfg(target_os = "linux")]
mod prepare;
mod registry;
pub mod runtime;
#[cfg(target_os = "linux")]
mod settle;

pub use eos_operation_core::WorkspaceExecutionBinding;
#[cfg(target_os = "linux")]
pub use ops::{CommandOps, ExecTarget};
pub use outcome::{ChangedPathKinds, WorkspaceConflict, WorkspaceTimings};
pub use runtime::{
    active_command_sessions_for_caller, cancel_all_command_sessions,
    cleanup_command_sessions_for_caller, command_ops, command_session_config,
    command_session_scratch_root, configure_command_sessions,
};
