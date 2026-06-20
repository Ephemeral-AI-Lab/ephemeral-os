mod contract;
mod core;
pub(crate) mod finalize;
mod helpers;
mod impls;
mod launch;
mod ownership;
mod process_store;
pub(crate) mod transcript;

pub use contract::{
    CancelCommandInput, CommandCallContext, CommandFinalizationOutcome, CommandFinalizedMetadata,
    CommandFinalizedPolicy, CommandId, CommandLinesOutput, CommandOutputSnapshot,
    CommandPollOutput, CommandStatus, CommandStream, CommandTranscriptRow,
    CommandWorkspaceDestroyMetadata, CommandYield, ExecCommandInput, PollCommandInput,
    ReadCommandLinesInput, WriteStdinInput,
};
pub use core::CommandOperationService;
pub use launch::{CommandLaunchDriver, RealCommandLaunchDriver};
pub use process_store::{
    ActiveCommandProcess, ActiveCommandRef, CancellationState, CommandCompletionStore,
    CommandFinalizePolicy, CommandLifecycleState, CommandProcessStore, CommandReservation,
    CommandTerminalResult, CommandTranscriptStore, CompletedCommandRecord, FinalizationState,
    RetainedCommandTranscript, DEFAULT_MAX_ACTIVE_COMMANDS,
};
