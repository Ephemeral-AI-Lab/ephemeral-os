pub mod contract;
pub mod error;
mod launch;
pub mod process_store;
pub mod registry;
pub mod remount;
pub mod service;
mod transcript;

pub(crate) mod exec;
pub(crate) mod finalize;

pub use contract::{
    CancelCommandInput, CommandCallContext, CommandFinalizationOutcome, CommandFinalizedMetadata,
    CommandFinalizedPolicy, CommandId, CommandLinesOutput, CommandOutputSnapshot,
    CommandPollOutput, CommandStatus, CommandStream, CommandTranscriptRow,
    CommandWorkspaceDestroyMetadata, CommandYield, ExecCommandInput, OperationTraceContext,
    PollCommandInput, ReadCommandLinesInput, WriteStdinInput,
};
pub use error::CommandServiceError;
pub use launch::{CommandLaunchDriver, RealCommandLaunchDriver};
pub use process_store::{
    ActiveCommandProcess, ActiveCommandRef, CancellationState, CommandCompletionStore,
    CommandFinalizePolicy, CommandLifecycleState, CommandProcessStore, CommandReservation,
    CommandTerminalResult, CommandTraceOrigin, CommandTranscriptStore, CompletedCommandRecord,
    FinalizationState, RetainedCommandTranscript, DEFAULT_MAX_ACTIVE_COMMANDS,
};
pub use registry::CommandRegistry;
pub use remount::{
    CommandRemountInspection, CommandRemountQuiesce, ProcessGroupController,
    RemountCancellationToken, RemountSwitchState,
};
pub use service::{CommandFinalizationOptions, CommandOperationService};
