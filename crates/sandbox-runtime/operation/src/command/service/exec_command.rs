use std::path::PathBuf;
use std::time::Instant;

use sandbox_runtime_namespace_execution::{NamespaceExecutionId, NamespaceTarget};

use crate::command::finalize::{build_on_complete, CommandFinalization, FinalizationTrace};
use crate::command::service::exec::ExecCommand;
use crate::command::service::CommandOperationService;
use crate::command::{CommandExecValue, CommandOutput, CommandServiceError, ExecCommandInput};
use crate::namespace_execution::{
    unix_ms, CompletedNamespaceExecutionMeta, NamespaceExecutionRecord,
    NamespaceExecutionTerminalStatus,
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

        let (entry, transcript_path) = match workspace
            .entry()
            .and_then(|entry| self.prepare_transcript_path(&id).map(|path| (entry, path)))
        {
            Ok(pair) => pair,
            Err(error) => {
                return Err(self.fail_command_start(
                    &id,
                    workspace,
                    origin_request_id.as_deref(),
                    error,
                ))
            }
        };

        let started_at = Instant::now();
        let started_at_unix_ms = unix_ms();
        let exec_command = ExecCommand {
            command: input.cmd.clone(),
            timeout_seconds: input.timeout_ms.map(|ms| ms as f64 / 1000.0),
            transcript_path: transcript_path.clone(),
            started_at,
        };
        let on_complete = build_on_complete(
            workspace.command_finalization(),
            self.workspace_handle().clone(),
            self.finalization_trace(&id, &workspace, origin_request_id.as_deref()),
            self.namespace_execution_store().clone(),
            CompletedNamespaceExecutionMeta {
                namespace_execution_id: id.clone(),
                workspace_session_id: workspace.workspace_session_id.clone(),
                operation_name: "exec_command".to_owned(),
                origin_request_id: origin_request_id.clone(),
                started_at_unix_ms,
            },
        );
        let target = NamespaceTarget::from(entry);

        let exec = measure_optional_if(trace, span_keys::COMMAND_EXEC_PROCESS_START, || {
            self.engine()
                .run_shell_interactive(exec_command, target, id.clone(), on_complete)
        });
        let exec = match exec {
            Ok(exec) => exec,
            Err(error) => {
                let error = CommandServiceError::CommandIo {
                    command_session_id: id.clone(),
                    error: error.to_string(),
                };
                self.cleanup_transcript_dir(&id);
                return Err(self.fail_command_start(
                    &id,
                    workspace,
                    origin_request_id.as_deref(),
                    error,
                ));
            }
        };

        self.engine().attach(
            &id,
            CommandExecValue::new(
                exec,
                transcript_path,
                workspace.workspace_session_id.clone(),
                started_at,
                "exec_command",
            ),
        );
        drop(admission_guard);

        self.wait_for_command_yield(id.clone(), input.yield_time_ms.unwrap_or(1000), 0, false)
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
    ) -> Option<FinalizationTrace> {
        let origin_request_id = origin_request_id?;
        let sink = self.async_trace_sink()?;
        Some(FinalizationTrace {
            sink,
            origin_request_id: origin_request_id.to_owned(),
            workspace_session_id: workspace.workspace_session_id.clone(),
            namespace_execution_id: id.clone(),
        })
    }

    fn prepare_transcript_path(
        &self,
        id: &NamespaceExecutionId,
    ) -> Result<PathBuf, CommandServiceError> {
        let command_dir = self.config().scratch_root.join(&id.0);
        std::fs::create_dir_all(&command_dir).map_err(|error| CommandServiceError::CommandIo {
            command_session_id: id.clone(),
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
        origin_request_id: Option<&str>,
        error: CommandServiceError,
    ) -> CommandServiceError {
        let now = unix_ms();
        let _ = self
            .namespace_execution_store()
            .record_completed(NamespaceExecutionRecord {
                namespace_execution_id: id.clone(),
                workspace_session_id: workspace.workspace_session_id.clone(),
                operation_name: "exec_command".to_owned(),
                origin_request_id: origin_request_id.map(str::to_owned),
                started_at_unix_ms: now,
                finished_at_unix_ms: Some(now),
                duration_ms: Some(0.0),
                terminal_status: Some(NamespaceExecutionTerminalStatus::Error),
                exit_code: None,
                error_kind: Some("command_start_failed".to_owned()),
                error_message: Some(
                    "command start failed before namespace execution started".to_owned(),
                ),
            });
        if !workspace.one_shot {
            return error;
        }
        match self.destroy_one_shot_workspace_session(workspace.handler) {
            Ok(_) => error,
            Err(cleanup_error) => CommandServiceError::OneShotSessionCleanupFailed {
                command_session_id: id.clone(),
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

    fn command_finalization(&self) -> CommandFinalization {
        if self.one_shot {
            CommandFinalization::DestroyOneShot(Box::new(self.handler.clone()))
        } else {
            CommandFinalization::KeepSession
        }
    }
}
