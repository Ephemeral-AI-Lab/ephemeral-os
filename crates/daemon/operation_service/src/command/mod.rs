pub mod contract;
pub mod error;
pub mod process_store;
pub mod registry;
pub mod service;

pub(crate) mod exec;
pub(crate) mod remount;

pub use contract::{
    CommandCallContext, CommandId, CommandStatus, CommandYield, ExecCommandInput,
    OperationTraceContext,
};
pub use error::CommandServiceError;
pub use process_store::{
    ActiveCommandProcess, ActiveCommandRef, CancellationState, CommandCompletionStore,
    CommandFinalizePolicy, CommandLifecycleState, CommandProcessStore, CommandReservation,
    CommandTerminalResult, CommandTraceOrigin, CommandTranscriptStore, CompletedCommandRecord,
    FinalizationState, RetainedCommandTranscript, DEFAULT_MAX_ACTIVE_COMMANDS,
};
pub use registry::CommandRegistry;
pub use service::{CommandFinalizationOptions, CommandOperationService};
