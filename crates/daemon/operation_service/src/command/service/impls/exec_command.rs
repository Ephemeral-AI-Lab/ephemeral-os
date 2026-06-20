use std::path::PathBuf;
use std::sync::{Arc, MutexGuard};
use std::time::Instant;

use command::process::{CommandProcess, CommandProcessExit, CommandProcessSpec};
use command::yield_wait_loop::WaitOutcome;

use crate::command::{
    ActiveCommandProcess, CancellationState, CommandCallContext, CommandFinalizePolicy, CommandId,
    CommandLifecycleState, CommandOutputSnapshot, CommandServiceError, CommandStatus,
    CommandTranscriptStore, CommandYield, ExecCommandInput, FinalizationState,
};
use crate::workspace_crate::{
    CallerId, CreateWorkspaceRequest, DestroyWorkspaceRequest, WorkspaceEntry, WorkspaceId,
    WorkspaceProfile,
};
use crate::workspace_session::WorkspaceSessionHandler;

use crate::command::service::CommandOperationService;

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
        if workspace.is_session_command
            && self
                .workspace()
                .is_remount_pending(&workspace.workspace_session_id)
        {
            return Err(CommandServiceError::WorkspaceSessionRemountPending {
                workspace_session_id: workspace.workspace_session_id.clone(),
            });
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
            WaitOutcome::Running(stdout) => Ok(CommandYield {
                command_id: Some(command_id),
                status: CommandStatus::Running,
                exit_code: None,
                output: CommandOutputSnapshot { stdout },
                finalized: None,
            }),
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
