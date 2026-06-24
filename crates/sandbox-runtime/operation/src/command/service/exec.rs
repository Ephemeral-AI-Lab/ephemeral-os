use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

use sandbox_runtime_command::CommandTerminalResult;
use sandbox_runtime_namespace_execution::{NamespaceExecutionError, RunnerOutcome, ShellOperation};

use crate::command::CommandSessionId;
use crate::observability::{
    measure_optional, AsyncTraceSink, CommandFinalizationTraceMetadata, OperationTrace,
};
use crate::workspace_crate::{DestroyWorkspaceRequest, WorkspaceSessionId};
use crate::workspace_session::{
    WorkspaceSessionError, WorkspaceSessionHandler, WorkspaceSessionService,
};

pub(crate) enum SessionDisposition {
    ExistingSession,
    OneShot(Box<WorkspaceSessionHandler>),
}

pub(crate) struct CommandFinalizationTrace {
    pub(crate) sink: AsyncTraceSink,
    pub(crate) origin_request_id: String,
    pub(crate) workspace_session_id: WorkspaceSessionId,
    pub(crate) command_session_id: CommandSessionId,
}

pub(crate) struct ExecCommand {
    command: String,
    timeout_seconds: Option<f64>,
    transcript_path: PathBuf,
    session_disposition: SessionDisposition,
    workspace: Arc<WorkspaceSessionService>,
    started_at: Instant,
    finalization_trace: Option<CommandFinalizationTrace>,
}

impl ExecCommand {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new(
        command: String,
        timeout_seconds: Option<f64>,
        transcript_path: PathBuf,
        session_disposition: SessionDisposition,
        workspace: Arc<WorkspaceSessionService>,
        started_at: Instant,
        finalization_trace: Option<CommandFinalizationTrace>,
    ) -> Self {
        Self {
            command,
            timeout_seconds,
            transcript_path,
            session_disposition,
            workspace,
            started_at,
            finalization_trace,
        }
    }
}

impl ShellOperation for ExecCommand {
    type Output = CommandTerminalResult;

    fn operation_name(&self) -> &'static str {
        "exec_command"
    }

    fn command(&self) -> &str {
        &self.command
    }

    fn timeout_seconds(&self) -> Option<f64> {
        self.timeout_seconds
    }

    fn transcript_path(&self) -> Option<&Path> {
        Some(&self.transcript_path)
    }

    fn finalize(
        self: Box<Self>,
        outcome: RunnerOutcome,
    ) -> Result<CommandTerminalResult, NamespaceExecutionError> {
        let result = CommandTerminalResult {
            status: outcome.status(),
            exit_code: outcome.exit_code(),
            command_total_time_seconds: self.started_at.elapsed().as_secs_f64(),
        };
        let ExecCommand {
            session_disposition,
            workspace,
            finalization_trace,
            ..
        } = *self;
        finalize_session(&workspace, session_disposition, finalization_trace, result)
    }
}

fn finalize_session(
    workspace: &WorkspaceSessionService,
    disposition: SessionDisposition,
    trace: Option<CommandFinalizationTrace>,
    result: CommandTerminalResult,
) -> Result<CommandTerminalResult, NamespaceExecutionError> {
    let Some(trace) = trace else {
        return apply_disposition(workspace, disposition)
            .map(|()| result)
            .map_err(finalize_error);
    };
    let op_trace = OperationTrace::new();
    let destroyed = measure_optional(
        Some(&op_trace),
        "complete_terminal_command_with_services",
        || {
            measure_optional(Some(&op_trace), "apply_workspace_completion_policy", || {
                apply_disposition(workspace, disposition)
            })
        },
    );
    let metadata = CommandFinalizationTraceMetadata {
        origin_request_id: trace.origin_request_id,
        workspace_session_id: Some(trace.workspace_session_id),
        command_session_id: trace.command_session_id,
        finalizer_error: destroyed.as_ref().err().map(ToString::to_string),
    };
    (trace.sink)(op_trace.complete(), metadata);
    destroyed.map(|()| result).map_err(finalize_error)
}

fn apply_disposition(
    workspace: &WorkspaceSessionService,
    disposition: SessionDisposition,
) -> Result<(), WorkspaceSessionError> {
    match disposition {
        SessionDisposition::ExistingSession => Ok(()),
        SessionDisposition::OneShot(handler) => workspace
            .destroy_session(*handler, DestroyWorkspaceRequest::default())
            .map(|_| ()),
    }
}

fn finalize_error(error: WorkspaceSessionError) -> NamespaceExecutionError {
    NamespaceExecutionError::Finalize(error.to_string())
}
