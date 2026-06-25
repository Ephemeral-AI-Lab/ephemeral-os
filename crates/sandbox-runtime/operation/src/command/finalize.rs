use std::sync::Arc;

use sandbox_runtime_namespace_execution::{NamespaceExecutionError, NamespaceExecutionId};

use crate::command::CommandTerminalResult;
use crate::namespace_execution::{
    CompletedNamespaceExecutionMeta, NamespaceExecutionLedger, NamespaceExecutionRecord,
};
use crate::observability::{
    measure_optional, AsyncTraceSink, CommandFinalizationTraceMetadata, OperationTrace,
};
use crate::workspace_crate::{DestroyWorkspaceRequest, WorkspaceSessionId};
use crate::workspace_session::{
    WorkspaceSessionError, WorkspaceSessionHandler, WorkspaceSessionService,
};

/// The workspace-completion policy a command applies once its child reaches a
/// terminal state. Closed at these two variants for this rework; `publish`
/// requires a `LayerStackService` collaborator that the command does not yet hold.
pub(crate) enum CommandFinalization {
    KeepSession,
    DestroyOneShot(Box<WorkspaceSessionHandler>),
}

impl CommandFinalization {
    fn apply(self, workspace: &WorkspaceSessionService) -> Result<(), WorkspaceSessionError> {
        match self {
            Self::KeepSession => Ok(()),
            Self::DestroyOneShot(handler) => workspace
                .destroy_session(*handler, DestroyWorkspaceRequest::default())
                .map(|_| ()),
        }
    }
}

/// The observability ingredients for the async command-finalization trace. Built
/// only when the originating request carried a request id and an async sink is
/// configured.
pub(crate) struct FinalizationTrace {
    pub(crate) sink: AsyncTraceSink,
    pub(crate) origin_request_id: String,
    pub(crate) workspace_session_id: WorkspaceSessionId,
    pub(crate) namespace_execution_id: NamespaceExecutionId,
}

/// Assemble the engine `on_complete` closure over three single-job units: apply
/// the workspace policy, emit the finalization trace (teardown errors go to
/// observability — never to the command result), then push the completed record
/// to the ledger projection.
pub(crate) fn build_on_complete(
    finalization: CommandFinalization,
    workspace: Arc<WorkspaceSessionService>,
    trace: Option<FinalizationTrace>,
    ledger: Arc<NamespaceExecutionLedger>,
    record_meta: CompletedNamespaceExecutionMeta,
) -> impl FnOnce(&Result<CommandTerminalResult, NamespaceExecutionError>) + Send + 'static {
    move |result| {
        let finalizer_error = finalization
            .apply(&workspace)
            .err()
            .map(|error| error.to_string());
        if let Some(trace) = trace {
            emit_finalization_trace(trace, finalizer_error);
        }
        let _ = ledger.record_completed(NamespaceExecutionRecord::completed(record_meta, result));
    }
}

fn emit_finalization_trace(trace: FinalizationTrace, finalizer_error: Option<String>) {
    let op_trace = OperationTrace::new();
    measure_optional(
        Some(&op_trace),
        "complete_terminal_command_with_services",
        || {
            measure_optional(Some(&op_trace), "apply_workspace_completion_policy", || {});
        },
    );
    let metadata = CommandFinalizationTraceMetadata {
        origin_request_id: trace.origin_request_id,
        workspace_session_id: trace.workspace_session_id,
        namespace_execution_id: trace.namespace_execution_id,
        finalizer_error,
    };
    (trace.sink)(op_trace.complete(), metadata);
}
