use std::path::PathBuf;
use std::time::Instant;

use sandbox_runtime_command::CommandExecution;
use sandbox_runtime_namespace_execution::{NamespaceExecutionId, NamespaceTarget};

use crate::command::service::exec::{CommandFinalizationTrace, ExecCommand, SessionDisposition};
use crate::command::service::{command_session_id, CommandOperationService};
use crate::command::{CommandOutput, CommandServiceError, ExecCommandInput};
use crate::namespace_execution::{
    BeginNamespaceExecution, CompleteNamespaceExecution, NamespaceExecutionTerminalStatus,
};
use crate::observability::{measure_optional_if, span_keys, OperationTrace};
use crate::workspace_crate::{WorkspaceEntry, WorkspaceSessionId};
use crate::workspace_session::WorkspaceSessionHandler;

impl CommandOperationService {
    pub fn exec_command(
        &self,
        input: ExecCommandInput,
        trace: Option<&OperationTrace>,
    ) -> Result<CommandOutput, CommandServiceError> {
        self.exec_command_with_origin_request_id(input, trace, None)
    }

    pub(crate) fn exec_command_with_origin_request_id(
        &self,
        input: ExecCommandInput,
        trace: Option<&OperationTrace>,
        origin_request_id: Option<String>,
    ) -> Result<CommandOutput, CommandServiceError> {
        if input.cmd.trim().is_empty() {
            return Err(CommandServiceError::InvalidCommand {
                message: "cmd must be non-empty".to_owned(),
            });
        }
        let existing_session_admission = input
            .workspace_session_id
            .is_some()
            .then(|| self.begin_workspace_lifecycle_admission());
        let workspace =
            measure_optional_if(trace, span_keys::COMMAND_EXEC_WORKSPACE_RESOLVE, || {
                self.resolve_exec_workspace(&input, trace)
            })?;
        let admission_guard = existing_session_admission
            .unwrap_or_else(|| self.begin_workspace_lifecycle_admission());

        let id = self.engine().allocate_id();
        let _ = self.namespace_execution_store().begin_namespace_execution(
            id.clone(),
            BeginNamespaceExecution {
                workspace_session_id: workspace.workspace_session_id.clone(),
                operation_name: "exec_command".to_owned(),
                origin_request_id: origin_request_id.clone(),
            },
        );

        let (entry, transcript_path) = match workspace
            .entry()
            .and_then(|entry| self.prepare_transcript_path(&id).map(|path| (entry, path)))
        {
            Ok(pair) => pair,
            Err(error) => return Err(self.fail_command_start(&id, workspace, error)),
        };

        let started_at = Instant::now();
        let exec_command = ExecCommand {
            command: input.cmd.clone(),
            timeout_seconds: input.timeout_ms.map(|ms| ms as f64 / 1000.0),
            transcript_path: transcript_path.clone(),
            session_disposition: workspace.session_disposition(),
            workspace: self.workspace_handle().clone(),
            started_at,
            finalization_trace: self.finalization_trace(
                &id,
                &workspace,
                origin_request_id.as_deref(),
            ),
        };
        let target = NamespaceTarget::from(entry);

        let exec = measure_optional_if(trace, span_keys::COMMAND_EXEC_PROCESS_START, || {
            self.engine()
                .run_shell_interactive(exec_command, target, id.clone())
        });
        let exec = match exec {
            Ok(exec) => exec,
            Err(error) => {
                let error = CommandServiceError::CommandIo {
                    command_session_id: command_session_id(&id),
                    error: error.to_string(),
                };
                self.cleanup_transcript_dir(&id);
                return Err(self.fail_command_start(&id, workspace, error));
            }
        };

        self.engine().attach(
            &id,
            CommandExecution::new(
                exec,
                Some(transcript_path),
                workspace.workspace_session_id.clone(),
                started_at,
            ),
        );
        drop(admission_guard);

        self.wait_for_command_yield(
            command_session_id(&id),
            input.yield_time_ms.unwrap_or(1000),
            0,
            false,
        )
    }

    fn resolve_exec_workspace(
        &self,
        input: &ExecCommandInput,
        trace: Option<&OperationTrace>,
    ) -> Result<ResolvedExecWorkspace, CommandServiceError> {
        let handler = if let Some(workspace_session_id) = &input.workspace_session_id {
            measure_optional_if(
                trace,
                span_keys::COMMAND_EXEC_WORKSPACE_RESOLVE_EXISTING_SESSION,
                || self.resolve_workspace_session(workspace_session_id.clone()),
            )?
        } else {
            measure_optional_if(
                trace,
                span_keys::COMMAND_EXEC_WORKSPACE_CREATE_ONE_SHOT_SESSION,
                || self.create_one_shot_workspace_session(),
            )?
        };
        Ok(ResolvedExecWorkspace {
            workspace_session_id: handler.workspace_session_id.clone(),
            handler,
            one_shot: input.workspace_session_id.is_none(),
        })
    }

    fn finalization_trace(
        &self,
        id: &NamespaceExecutionId,
        workspace: &ResolvedExecWorkspace,
        origin_request_id: Option<&str>,
    ) -> Option<CommandFinalizationTrace> {
        let origin_request_id = origin_request_id?;
        let sink = self.async_trace_sink()?;
        Some(CommandFinalizationTrace {
            sink,
            origin_request_id: origin_request_id.to_owned(),
            workspace_session_id: workspace.workspace_session_id.clone(),
            command_session_id: command_session_id(id),
        })
    }

    fn prepare_transcript_path(
        &self,
        id: &NamespaceExecutionId,
    ) -> Result<PathBuf, CommandServiceError> {
        let command_dir = self.config().scratch_root.join(&id.0);
        std::fs::create_dir_all(&command_dir).map_err(|error| CommandServiceError::CommandIo {
            command_session_id: command_session_id(id),
            error: error.to_string(),
        })?;
        Ok(command_dir.join("transcript.log"))
    }

    fn cleanup_transcript_dir(&self, id: &NamespaceExecutionId) {
        let command_dir = self.config().scratch_root.join(&id.0);
        let _ = std::fs::remove_dir_all(command_dir);
    }

    fn fail_command_start(
        &self,
        id: &NamespaceExecutionId,
        workspace: ResolvedExecWorkspace,
        error: CommandServiceError,
    ) -> CommandServiceError {
        let _ = self
            .namespace_execution_store()
            .complete_namespace_execution(
                id,
                CompleteNamespaceExecution {
                    terminal_status: NamespaceExecutionTerminalStatus::Error,
                    exit_code: None,
                    error_kind: Some("command_start_failed".to_owned()),
                    error_message: Some(
                        "command start failed before namespace execution started".to_owned(),
                    ),
                },
            );
        if !workspace.one_shot {
            return error;
        }
        match self.destroy_one_shot_workspace_session(workspace.handler) {
            Ok(_) => error,
            Err(cleanup_error) => CommandServiceError::OneShotSessionCleanupFailed {
                command_session_id: command_session_id(id),
                command_error: Box::new(error),
                cleanup_error: cleanup_error.to_string(),
            },
        }
    }
}

struct ResolvedExecWorkspace {
    handler: WorkspaceSessionHandler,
    workspace_session_id: WorkspaceSessionId,
    one_shot: bool,
}

impl ResolvedExecWorkspace {
    fn entry(&self) -> Result<WorkspaceEntry, CommandServiceError> {
        self.handler
            .handle
            .entry()
            .map_err(|error| CommandServiceError::InvalidCommand {
                message: error.to_string(),
            })
    }

    fn session_disposition(&self) -> SessionDisposition {
        if self.one_shot {
            SessionDisposition::OneShot(Box::new(self.handler.clone()))
        } else {
            SessionDisposition::ExistingSession
        }
    }
}
