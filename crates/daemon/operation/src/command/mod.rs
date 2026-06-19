#![forbid(unsafe_code)]

pub mod contract;

mod command_workspace;
mod finalize;
mod outcome;
mod prepare;
mod registry;
mod service;
mod trace;

pub use command_workspace::OneShotCommandWorkspace;
pub use contract::{
    CollectCompletedOutput, CommandCompletion, CommandMetadata, CommandResponse, CommandStatus,
};
pub use outcome::{ChangedPathKinds, WorkspaceConflict, WorkspaceTimings};
pub use service::{
    CommandExecError, CommandExecOutcome, CommandOps, CommandProgressTraceFacts,
    CommandReadProgressOutcome, CommandRemountInspection, CommandRemountQuiesce,
    CommandStdinTraceFacts, CommandWriteStdinOutcome, ExecTarget,
};
pub use trace::CommandTraceEvent;
