use std::path::PathBuf;
use std::sync::{Arc, MutexGuard};
use std::time::Instant;

use command::process::{CommandProcess, CommandProcessExit, CommandProcessSpec};
use command::yield_wait_loop::WaitOutcome;

use super::command_yield_response;
use crate::command::{
    ActiveCommandProcess, CancellationState, CommandCallContext, CommandFinalizePolicy, CommandId,
    CommandLifecycleState, CommandOutputSnapshot, CommandServiceError, CommandTranscriptStore,
    CommandYield, ExecCommandInput, FinalizationState,
};
use crate::operation::{
    ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationFamily, OperationRequest, OperationResponse,
    OperationSpec,
};
use crate::workspace_crate::{
    CallerId, CreateWorkspaceRequest, DestroyWorkspaceRequest, WorkspaceEntry, WorkspaceId,
    WorkspaceProfile,
};
use crate::workspace_session::WorkspaceSessionHandler;

use crate::command::service::CommandOperationService;
use crate::DaemonOperations;

pub(crate) const SPEC: OperationSpec = OperationSpec {
    name: "exec_command",
    family: OperationFamily::Command,
    summary: "Start a command in a workspace.",
    args: EXEC_COMMAND_ARGS,
    cli: Some(EXEC_COMMAND_CLI),
};

const EXEC_COMMAND_ARGS: &[ArgSpec] = &[
    ArgSpec::optional(
        "caller_id",
        ArgKind::String,
        "Command owner used for follow-up command operations.",
        Some(""),
        Some(ArgCliSpec {
            flag: Some("--caller-id"),
            positional: None,
        }),
    ),
    ArgSpec::required(
        "workspace_root",
        ArgKind::Path,
        "Workspace root and layer-stack root for one-shot command workspace creation.",
        Some(ArgCliSpec {
            flag: Some("--workspace-root"),
            positional: None,
        }),
    ),
    ArgSpec::optional(
        "workspace_session_id",
        ArgKind::String,
        "Existing workspace session id to run inside.",
        None,
        Some(ArgCliSpec {
            flag: Some("--workspace-session-id"),
            positional: None,
        }),
    ),
    ArgSpec::required(
        "cmd",
        ArgKind::String,
        "Shell command text.",
        Some(ArgCliSpec {
            flag: None,
            positional: Some("COMMAND"),
        }),
    ),
    ArgSpec::optional(
        "cwd",
        ArgKind::Path,
        "Command working directory.",
        None,
        Some(ArgCliSpec {
            flag: Some("--cwd"),
            positional: None,
        }),
    ),
    ArgSpec::optional(
        "timeout_seconds",
        ArgKind::Float,
        "Command timeout in seconds.",
        None,
        Some(ArgCliSpec {
            flag: Some("--timeout-seconds"),
            positional: None,
        }),
    ),
    ArgSpec::optional(
        "yield_time_ms",
        ArgKind::Integer,
        "Initial output wait in milliseconds.",
        None,
        Some(ArgCliSpec {
            flag: Some("--yield-time-ms"),
            positional: None,
        }),
    ),
];

const EXEC_COMMAND_CLI: CliSpec = CliSpec {
    path: &["daemon", "commands", "exec"],
    usage: "ephai-sandbox-gateway daemon --sandbox-id SID commands exec --workspace-root PATH [--caller-id ID] [--workspace-session-id ID] [--cwd PATH] [--timeout-seconds S] [--yield-time-ms MS] -- COMMAND",
    examples: &[
        "ephai-sandbox-gateway daemon --sandbox-id sb-1 commands exec --workspace-root /testbed -- pwd",
    ],
};

pub(crate) fn dispatch(
    operations: &DaemonOperations,
    request: OperationRequest<'_>,
) -> OperationResponse {
    let input = match parse_input(&request) {
        Ok(input) => input,
        Err(response) => return response,
    };
    let context = CommandCallContext {
        caller_id: input.caller_id.clone(),
    };
    command_yield_response(&request, operations.command.exec_command(input, context))
}

fn parse_input(request: &OperationRequest<'_>) -> Result<ExecCommandInput, OperationResponse> {
    let caller_id = request.optional_string("caller_id")?.unwrap_or_default();
    let workspace_session_id = request
        .optional_string("workspace_session_id")?
        .map(WorkspaceId);
    Ok(ExecCommandInput {
        caller_id: CallerId(caller_id),
        workspace_root: request.required_path("workspace_root")?,
        workspace_session_id,
        cmd: request.required_string("cmd")?,
        cwd: request.optional_path("cwd")?,
        timeout_seconds: request.optional_f64("timeout_seconds")?,
        yield_time_ms: request.optional_u64("yield_time_ms")?,
    })
}

impl CommandOperationService {
    pub fn exec_command(
        &self,
        input: ExecCommandInput,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError> {
        if input.cmd.trim().is_empty() {
            return Err(CommandServiceError::InvalidCommand {
                message: "cmd must be non-empty".to_owned(),
            });
        }
        if input.caller_id != context.caller_id {
            return Err(CommandServiceError::InvalidCommand {
                message: "exec caller must match command context".to_owned(),
            });
        }

        self.exec_validated_command(input, context)
    }

    fn exec_validated_command(
        &self,
        input: ExecCommandInput,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError> {
        let workspace = self.resolve_exec_workspace(&input, &context)?;
        let admission_guard = self.command_admission_guard(&workspace)?;
        let command_id = self.process_store().allocate_command_id();
        let reservation = match self.process_store().try_reserve() {
            Ok(reservation) => reservation,
            Err(error) => {
                return Err(self.cleanup_workspace_start_failure(
                    &command_id,
                    workspace,
                    None,
                    error,
                ));
            }
        };
        let started = match self.start_command_process(&command_id, &input, &workspace) {
            Ok(started) => started,
            Err(error) => {
                return Err(self.cleanup_workspace_start_failure(
                    &command_id,
                    workspace,
                    None,
                    error,
                ));
            }
        };

        if self.process_store().active(&command_id).is_some() {
            started.process.cancel_process();
            return Err(self.cleanup_workspace_start_failure(
                &command_id,
                workspace,
                Some(&started.process),
                CommandServiceError::DuplicateCommandId {
                    command_id: command_id.clone(),
                },
            ));
        }
        let (record, process_for_rollback) =
            started.into_active_record(command_id.clone(), context.caller_id.clone(), &workspace);
        if let Err(error) = self.process_store().insert_active(reservation, record) {
            process_for_rollback.cancel_process();
            return Err(self.cleanup_workspace_start_failure(
                &command_id,
                workspace,
                Some(process_for_rollback.as_ref()),
                error,
            ));
        }
        drop(admission_guard);

        self.initial_exec_yield(command_id, input.yield_time_ms)
    }

    fn resolve_exec_workspace(
        &self,
        input: &ExecCommandInput,
        context: &CommandCallContext,
    ) -> Result<ResolvedExecWorkspace, CommandServiceError> {
        match input.workspace_session_id.clone() {
            Some(workspace_session_id) => {
                let handler = self
                    .workspace()
                    .resolve_session(workspace_session_id, context.caller_id.clone())?;
                validate_workspace_root(input, &handler)?;
                Ok(ResolvedExecWorkspace::new(handler, true))
            }
            None => {
                let handler =
                    self.workspace()
                        .create_workspace_session(CreateWorkspaceRequest {
                            caller_id: context.caller_id.clone(),
                            layer_stack_root: input.workspace_root.clone(),
                            workspace_root: input.workspace_root.clone(),
                            profile: WorkspaceProfile::HostCompatible,
                        })?;
                Ok(ResolvedExecWorkspace::new(handler, false))
            }
        }
    }

    fn command_admission_guard(
        &self,
        workspace: &ResolvedExecWorkspace,
    ) -> Result<Option<MutexGuard<'_, ()>>, CommandServiceError> {
        let guard = if workspace.is_session_command {
            Some(self.lock_remount_admission())
        } else {
            None
        };
        if workspace.is_session_command {
            self.ensure_workspace_session_not_remount_pending(&workspace.workspace_session_id)?;
        }
        Ok(guard)
    }

    fn start_command_process(
        &self,
        command_id: &CommandId,
        input: &ExecCommandInput,
        workspace: &ResolvedExecWorkspace,
    ) -> Result<StartedCommand, CommandServiceError> {
        let process = self.launch_driver().spawn(
            CommandProcessSpec {
                id: command_id.0.clone(),
                caller_id: input.caller_id.0.clone(),
                command: input.cmd.clone(),
                cwd: input.cwd.clone(),
                timeout_seconds: input.timeout_seconds,
            },
            workspace.entry()?,
            self.config(),
        )?;
        Ok(StartedCommand::new(process))
    }

    fn initial_exec_yield(
        &self,
        command_id: CommandId,
        yield_time_ms: Option<u64>,
    ) -> Result<CommandYield, CommandServiceError> {
        let wait_ms = yield_time_ms.unwrap_or(self.config().default_yield_time_ms);
        let process = self
            .process_store()
            .active_process(&command_id)
            .ok_or_else(|| CommandServiceError::CommandNotFound {
                command_id: command_id.clone(),
            })?;
        let outcome = self.launch_driver().wait_for_initial_yield(
            process.as_ref(),
            self.config(),
            wait_ms,
            0,
        );

        match outcome {
            WaitOutcome::Running(stdout) => Ok(Self::running_command_yield(command_id, stdout)),
            WaitOutcome::Completed(process_exit) => {
                self.completed_initial_exec_yield(command_id, process_exit)
            }
        }
    }

    fn completed_initial_exec_yield(
        &self,
        command_id: CommandId,
        process_exit: CommandProcessExit,
    ) -> Result<CommandYield, CommandServiceError> {
        let result = self.finalize_command(command_id.clone(), process_exit)?;
        let finalized = self
            .process_store()
            .completed(&command_id)
            .and_then(|completed| completed.finalized);
        Ok(CommandYield {
            command_id: Some(command_id),
            status: result.status,
            exit_code: result.exit_code,
            output: CommandOutputSnapshot {
                stdout: result.stdout,
            },
            finalized,
        })
    }

    fn cleanup_workspace_start_failure(
        &self,
        command_id: &CommandId,
        workspace: ResolvedExecWorkspace,
        process: Option<&CommandProcess>,
        error: CommandServiceError,
    ) -> CommandServiceError {
        let error = if let Some(process) = process {
            cleanup_process_artifacts_after_start_failure(command_id, process, error)
        } else {
            error
        };
        self.cleanup_one_shot_workspace_after_start_failure(
            command_id,
            workspace.is_session_command,
            workspace.handler,
            error,
        )
    }

    fn cleanup_one_shot_workspace_after_start_failure(
        &self,
        command_id: &CommandId,
        is_session_command: bool,
        handler: WorkspaceSessionHandler,
        error: CommandServiceError,
    ) -> CommandServiceError {
        if is_session_command {
            return error;
        }

        match self
            .workspace()
            .destroy_session(handler, DestroyWorkspaceRequest::default())
        {
            Ok(_) => error,
            Err(cleanup_error) => CommandServiceError::OneShotWorkspaceCleanupFailed {
                command_id: command_id.clone(),
                command_error: Box::new(error),
                cleanup_error,
            },
        }
    }
}

struct ResolvedExecWorkspace {
    handler: WorkspaceSessionHandler,
    is_session_command: bool,
    workspace_session_id: WorkspaceId,
    workspace_root: PathBuf,
    finalize_policy: CommandFinalizePolicy,
}

impl ResolvedExecWorkspace {
    fn new(handler: WorkspaceSessionHandler, is_session_command: bool) -> Self {
        let workspace_session_id = handler.workspace_session_id.clone();
        let workspace_root = handler.handle.workspace_root.clone();
        let finalize_policy = finalize_policy(is_session_command, &workspace_session_id);
        Self {
            handler,
            is_session_command,
            workspace_session_id,
            workspace_root,
            finalize_policy,
        }
    }

    fn entry(&self) -> Result<WorkspaceEntry, CommandServiceError> {
        self.handler
            .handle
            .entry()
            .map_err(|error| CommandServiceError::InvalidCommand {
                message: error.to_string(),
            })
    }
}

struct StartedCommand {
    process: CommandProcess,
    transcript_path: Option<PathBuf>,
}

impl StartedCommand {
    fn new(process: CommandProcess) -> Self {
        let transcript_path = process.transcript_path().map(std::path::Path::to_path_buf);
        Self {
            process,
            transcript_path,
        }
    }

    fn into_active_record(
        self,
        command_id: CommandId,
        caller_id: CallerId,
        workspace: &ResolvedExecWorkspace,
    ) -> (ActiveCommandProcess, Arc<CommandProcess>) {
        let process = Arc::new(self.process);
        let process_for_rollback = Arc::clone(&process);
        let record = ActiveCommandProcess {
            command_id,
            caller_id,
            workspace_session_id: workspace.workspace_session_id.clone(),
            workspace_root: workspace.workspace_root.clone(),
            process,
            transcript: CommandTranscriptStore {
                transcript_path: self.transcript_path,
            },
            finalize_policy: workspace.finalize_policy.clone(),
            lifecycle_state: CommandLifecycleState::Running,
            cancellation: CancellationState::None,
            remount_cancellation: None,
            remount_switch_state: None,
            finalization: FinalizationState::NotStarted,
            started_at: Instant::now(),
        };
        (record, process_for_rollback)
    }
}

fn validate_workspace_root(
    input: &ExecCommandInput,
    handler: &WorkspaceSessionHandler,
) -> Result<(), CommandServiceError> {
    if handler.handle.workspace_root != input.workspace_root {
        return Err(CommandServiceError::WorkspaceRootMismatch {
            expected: handler.handle.workspace_root.clone(),
            actual: input.workspace_root.clone(),
        });
    }

    Ok(())
}

fn cleanup_process_artifacts_after_start_failure(
    command_id: &CommandId,
    process: &CommandProcess,
    error: CommandServiceError,
) -> CommandServiceError {
    match process.cleanup_artifacts_after_start_failure() {
        Ok(()) => error,
        Err(cleanup_error) => CommandServiceError::CommandArtifactCleanupFailed {
            command_id: command_id.clone(),
            command_error: Box::new(error),
            artifact_dir: process.artifact_dir(),
            cleanup_error: cleanup_error.to_string(),
        },
    }
}

fn finalize_policy(
    is_session_command: bool,
    workspace_session_id: &WorkspaceId,
) -> CommandFinalizePolicy {
    if is_session_command {
        CommandFinalizePolicy::Session {
            workspace_session_id: workspace_session_id.clone(),
        }
    } else {
        CommandFinalizePolicy::OneShotPublishThenDestroy {
            workspace_session_id: workspace_session_id.clone(),
        }
    }
}
