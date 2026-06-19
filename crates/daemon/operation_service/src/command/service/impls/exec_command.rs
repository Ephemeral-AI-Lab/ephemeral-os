use std::fs;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;

use command::process::{
    CommandProcess, CommandProcessExit, CommandProcessSpawn, CommandProcessSpec,
};
use command::yield_wait_loop::WaitOutcome;

use crate::command::{
    ActiveCommandProcess, CancellationState, CommandCallContext, CommandFinalizePolicy, CommandId,
    CommandLifecycleState, CommandOutputSnapshot, CommandServiceError, CommandStatus,
    CommandTranscriptStore, CommandYield, ExecCommandInput, FinalizationState,
};
use crate::workspace_crate::{
    CreateWorkspaceRequest, DestroyWorkspaceRequest, WorkspaceEntry, WorkspaceId, WorkspaceProfile,
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

        let mode = match input.workspace_session_id.clone() {
            Some(workspace_session_id) => {
                let handler = self
                    .workspace()
                    .resolve_session(workspace_session_id, context.caller_id.clone())?;
                validate_workspace_root(&input, &handler)?;
                ExecCommandMode::Session {
                    handler: Box::new(handler),
                }
            }
            None => ExecCommandMode::OneShot,
        };

        self.exec_resolved_command(input, mode, context)
    }

    fn exec_resolved_command(
        &self,
        input: ExecCommandInput,
        mode: ExecCommandMode,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError> {
        let is_session_command = mode.is_session();
        let handler = match mode {
            ExecCommandMode::Session { handler } => *handler,
            ExecCommandMode::OneShot => {
                self.workspace()
                    .create_workspace_session(CreateWorkspaceRequest {
                        caller_id: context.caller_id.clone(),
                        layer_stack_root: input.workspace_root.clone(),
                        workspace_root: input.workspace_root.clone(),
                        profile: WorkspaceProfile::HostCompatible,
                    })?
            }
        };
        let admission_guard = if is_session_command {
            Some(self.lock_remount_admission())
        } else {
            None
        };
        if is_session_command
            && self
                .workspace()
                .is_remount_pending(&handler.workspace_session_id)
        {
            return Err(CommandServiceError::WorkspaceSessionRemountPending {
                workspace_session_id: handler.workspace_session_id.clone(),
            });
        }
        let command_id = self.process_store().allocate_command_id();
        let reservation = match self.process_store().try_reserve() {
            Ok(reservation) => reservation,
            Err(error) => {
                return Err(self.cleanup_start_failure(
                    &command_id,
                    is_session_command,
                    handler,
                    None,
                    error,
                ));
            }
        };
        let workspace_session_id = handler.workspace_session_id.clone();
        let workspace_root = handler.handle.workspace_root.clone();
        let finalize_policy = finalize_policy(is_session_command, &workspace_session_id);
        let launch = match self.prepare_launch_context(&handler, &command_id) {
            Ok(launch) => launch,
            Err(error) => {
                return Err(self.cleanup_start_failure(
                    &command_id,
                    is_session_command,
                    handler,
                    None,
                    error,
                ));
            }
        };
        let process = match self.spawn_command_process(&command_id, &input, &launch) {
            Ok(process) => process,
            Err(error) => {
                return Err(self.cleanup_start_failure(
                    &command_id,
                    is_session_command,
                    handler,
                    Some(&launch),
                    error,
                ));
            }
        };

        if self.process_store().active(&command_id).is_some() {
            process.cancel_process();
            return Err(self.cleanup_start_failure(
                &command_id,
                is_session_command,
                handler,
                Some(&launch),
                CommandServiceError::DuplicateCommandId {
                    command_id: command_id.clone(),
                },
            ));
        }
        let process = Arc::new(process);
        let process_for_rollback = Arc::clone(&process);
        let record = ActiveCommandProcess {
            command_id: command_id.clone(),
            caller_id: context.caller_id.clone(),
            workspace_session_id,
            workspace_root,
            process,
            transcript: CommandTranscriptStore {
                transcript_path: Some(launch.transcript_path.clone()),
            },
            finalize_policy,
            lifecycle_state: CommandLifecycleState::Running,
            cancellation: CancellationState::None,
            remount_cancellation: None,
            remount_switch_state: None,
            finalization: FinalizationState::NotStarted,
            started_at: Instant::now(),
        };
        if let Err(error) = self.process_store().insert_active(reservation, record) {
            process_for_rollback.cancel_process();
            return Err(self.cleanup_start_failure(
                &command_id,
                is_session_command,
                handler,
                Some(&launch),
                error,
            ));
        }
        drop(admission_guard);

        self.initial_exec_yield(command_id, input.yield_time_ms)
    }

    fn prepare_launch_context(
        &self,
        handler: &WorkspaceSessionHandler,
        command_id: &CommandId,
    ) -> Result<PreparedCommandLaunch, CommandServiceError> {
        let workspace_entry =
            handler
                .handle
                .entry()
                .map_err(|error| CommandServiceError::InvalidCommand {
                    message: error.to_string(),
                })?;
        let command_dir = self.config().scratch_root.join(&command_id.0);
        fs::create_dir_all(&command_dir).map_err(|error| CommandServiceError::CommandIo {
            command_id: command_id.clone(),
            error: format!("prepare command artifact directory: {error}"),
        })?;
        Ok(PreparedCommandLaunch::new(command_dir, workspace_entry))
    }

    fn spawn_command_process(
        &self,
        command_id: &CommandId,
        input: &ExecCommandInput,
        launch: &PreparedCommandLaunch,
    ) -> Result<CommandProcess, CommandServiceError> {
        self.launch_driver().spawn(
            CommandProcessSpec {
                id: command_id.0.clone(),
                caller_id: input.caller_id.0.clone(),
                command: input.cmd.clone(),
                cwd: input.cwd.clone(),
                timeout_seconds: input.timeout_seconds,
            },
            CommandProcessSpawn {
                workspace_entry: launch.workspace_entry.clone(),
                request_path: launch.request_path.clone(),
                output_path: launch.output_path.clone(),
                final_path: launch.final_path.clone(),
                transcript_path: launch.transcript_path.clone(),
                transcript_timestamp_timezone: &self.config().transcript_timestamp_timezone,
                output_drain_grace_ms: self.config().output_drain_grace_ms,
            },
        )
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

    fn cleanup_start_failure(
        &self,
        command_id: &CommandId,
        is_session_command: bool,
        handler: WorkspaceSessionHandler,
        launch: Option<&PreparedCommandLaunch>,
        error: CommandServiceError,
    ) -> CommandServiceError {
        let error = if let Some(launch) = launch {
            launch.cleanup_artifacts_after_start_failure(command_id, error)
        } else {
            error
        };
        self.cleanup_one_shot_workspace_after_start_failure(
            command_id,
            is_session_command,
            handler,
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

enum ExecCommandMode {
    Session {
        handler: Box<WorkspaceSessionHandler>,
    },
    OneShot,
}

impl ExecCommandMode {
    fn is_session(&self) -> bool {
        matches!(self, Self::Session { .. })
    }
}

struct PreparedCommandLaunch {
    command_dir: PathBuf,
    workspace_entry: WorkspaceEntry,
    request_path: PathBuf,
    output_path: PathBuf,
    final_path: PathBuf,
    transcript_path: PathBuf,
}

impl PreparedCommandLaunch {
    fn new(command_dir: PathBuf, workspace_entry: WorkspaceEntry) -> Self {
        Self {
            command_dir: command_dir.clone(),
            workspace_entry,
            request_path: command_dir.join("command-request.json"),
            output_path: command_dir.join("runner-result.json"),
            final_path: command_dir.join("final.json"),
            transcript_path: command_dir.join("transcript.log"),
        }
    }

    fn cleanup_artifacts_after_start_failure(
        &self,
        command_id: &CommandId,
        error: CommandServiceError,
    ) -> CommandServiceError {
        match fs::remove_dir_all(&self.command_dir) {
            Ok(()) => error,
            Err(cleanup_error) if cleanup_error.kind() == std::io::ErrorKind::NotFound => error,
            Err(cleanup_error) => CommandServiceError::CommandArtifactCleanupFailed {
                command_id: command_id.clone(),
                command_error: Box::new(error),
                artifact_dir: self.command_dir.clone(),
                cleanup_error: cleanup_error.to_string(),
            },
        }
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
