mod error;
mod service;

use crate::operation::OperationFamilySpec;

pub use error::CommandServiceError;
pub(crate) use service::{
    ActiveCommandProcess, ActiveCommandRef, CancellationState, CommandLifecycleState,
    CommandProcessStore, CommandTerminalResult, CommandTranscriptStore, CompletedCommandRecord,
    FinalizationState, RetainedCommandTranscript,
};
pub use service::{
    CancelCommandInput, CommandFinalizedMetadata, CommandLinesOutput, CommandOperationService,
    CommandOutputSnapshot, CommandPollOutput, CommandPublishFinalization, CommandPublishStatus,
    CommandSessionId, CommandStatus, CommandStream, CommandTranscriptRow, CommandYield,
    ExecCommandInput, PollCommandInput, ReadCommandLinesInput, WriteCommandStdinInput,
};
pub use service::{CommandLaunchDriver, RealCommandLaunchDriver};

pub(crate) const COMMAND_FAMILY: OperationFamilySpec = OperationFamilySpec {
    id: "command",
    title: "Command",
    summary: "Run, interact with, inspect, and cancel commands.",
    description:
        "Run, interact with, inspect, and cancel commands inside the active sandbox runtime.",
};

const FAMILIES: &[&OperationFamilySpec] = &[&COMMAND_FAMILY];

pub(crate) fn operation_entries() -> &'static [crate::operation::OperationEntry] {
    service::operation_entries()
}

pub(crate) const fn operation_families() -> &'static [&'static OperationFamilySpec] {
    FAMILIES
}

pub(crate) fn operation_specs() -> &'static [&'static crate::operation::OperationSpec] {
    service::operation_specs()
}
