pub mod contract;
pub mod error;
pub(crate) mod finalize;
mod launch;
pub mod process_store;
pub(crate) mod remount;
pub mod service;
mod transcript;

pub use contract::{
    CancelCommandInput, CommandCallContext, CommandFinalizationOutcome, CommandFinalizedMetadata,
    CommandFinalizedPolicy, CommandId, CommandLinesOutput, CommandOutputSnapshot,
    CommandPollOutput, CommandStatus, CommandStream, CommandTranscriptRow,
    CommandWorkspaceDestroyMetadata, CommandYield, ExecCommandInput, PollCommandInput,
    ReadCommandLinesInput, WriteStdinInput,
};
pub use error::CommandServiceError;
pub use launch::{CommandLaunchDriver, RealCommandLaunchDriver};
pub use process_store::{
    ActiveCommandProcess, ActiveCommandRef, CancellationState, CommandCompletionStore,
    CommandFinalizePolicy, CommandLifecycleState, CommandProcessStore, CommandReservation,
    CommandTerminalResult, CommandTranscriptStore, CompletedCommandRecord, FinalizationState,
    RetainedCommandTranscript, DEFAULT_MAX_ACTIVE_COMMANDS,
};
pub use remount::{
    CommandRemountInspection, CommandRemountQuiesce, ProcessGroupController,
    RemountCancellationToken, RemountSwitchState,
};
pub use service::CommandOperationService;
