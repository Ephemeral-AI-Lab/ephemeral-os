mod error;
mod service;

use crate::operation::CliOperationFamilySpec;

pub use error::CommandServiceError;
pub(crate) use service::{
    ActiveCommandProcess, ActiveCommandRef, CancellationState, CommandLifecycleState,
    CommandProcessStore, CommandTerminalResult, CommandTranscriptStore, CommandWorkspaceOwnership,
    CompletedCommandRecord, FinalizationState, RetainedCommandTranscript,
};
pub use service::{
    CommandFinalizedMetadata, CommandLinesOutput, CommandOperationService, CommandOutputSnapshot,
    CommandPublishFinalization, CommandPublishStatus, CommandSessionId, CommandStatus,
    CommandYield, ExecCommandInput, ReadCommandLinesInput, WriteCommandStdinInput,
};
pub use service::{CommandLaunchDriver, RealCommandLaunchDriver};

pub(crate) const COMMAND_FAMILY: CliOperationFamilySpec = CliOperationFamilySpec {
    id: "command",
    title: "Command",
    summary: "Run, interact with, and inspect commands.",
    description: "Run, interact with, and inspect commands inside the active sandbox runtime.",
};

const FAMILIES: &[&CliOperationFamilySpec] = &[&COMMAND_FAMILY];

pub(crate) fn operation_entries() -> &'static [crate::operation::OperationEntry] {
    service::operation_entries()
}

pub(crate) const fn cli_operation_families() -> &'static [&'static CliOperationFamilySpec] {
    FAMILIES
}

pub(crate) fn cli_operation_specs() -> &'static [&'static crate::operation::CliOperationSpec] {
    service::cli_operation_specs()
}
