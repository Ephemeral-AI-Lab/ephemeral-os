use std::sync::Arc;
use std::time::Instant;

use crate::command::{
    CancelCommandInput, CancellationState, CommandCallContext, CommandLaunchDriver,
    CommandLifecycleState, CommandLinesOutput, CommandOutputSnapshot, CommandPollOutput,
    CommandProcessStore, CommandRegistry, CommandServiceError, CommandStatus, CommandYield,
    CompletedCommandRecord, FinalizationState, PollCommandInput, ReadCommandLinesInput,
    RealCommandLaunchDriver, WriteStdinInput,
};
use crate::workspace_crate::CallerId;
use crate::workspace_manager::WorkspaceManagerService;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct CommandFinalizationOptions {
    pub one_shot_capture: layerstack::service::BoundedCaptureOptions,
    pub one_shot_publish: layerstack::CommitOptions,
}

pub struct CommandOperationService {
    workspace: Arc<WorkspaceManagerService>,
    config: ::command::CommandConfig,
    registry: Arc<CommandRegistry>,
    process_store: Arc<CommandProcessStore>,
    launch_driver: Arc<dyn CommandLaunchDriver>,
    finalization_options: CommandFinalizationOptions,
}

impl CommandOperationService {
    #[must_use]
    pub fn new(workspace: Arc<WorkspaceManagerService>, config: ::command::CommandConfig) -> Self {
        Self::with_finalization_options(workspace, config, CommandFinalizationOptions::default())
    }

    #[must_use]
    pub fn with_finalization_options(
        workspace: Arc<WorkspaceManagerService>,
        config: ::command::CommandConfig,
        finalization_options: CommandFinalizationOptions,
    ) -> Self {
        Self {
            workspace,
            config,
            registry: Arc::new(CommandRegistry::new()),
            process_store: Arc::new(CommandProcessStore::new()),
            launch_driver: Arc::new(RealCommandLaunchDriver),
            finalization_options,
        }
    }

    #[doc(hidden)]
    #[must_use]
    pub fn with_launch_driver_for_test(
        workspace: Arc<WorkspaceManagerService>,
        config: ::command::CommandConfig,
        launch_driver: Arc<dyn CommandLaunchDriver>,
    ) -> Self {
        Self {
            workspace,
            config,
            registry: Arc::new(CommandRegistry::new()),
            process_store: Arc::new(CommandProcessStore::new()),
            launch_driver,
            finalization_options: CommandFinalizationOptions::default(),
        }
    }

    #[cfg(test)]
    pub(crate) fn with_process_store_for_test(
        workspace: Arc<WorkspaceManagerService>,
        config: ::command::CommandConfig,
        process_store: CommandProcessStore,
    ) -> Self {
        Self::with_process_store_and_launch_driver_for_test(
            workspace,
            config,
            process_store,
            Arc::new(RealCommandLaunchDriver),
        )
    }

    #[cfg(test)]
    pub(crate) fn with_process_store_and_launch_driver_for_test(
        workspace: Arc<WorkspaceManagerService>,
        config: ::command::CommandConfig,
        process_store: CommandProcessStore,
        launch_driver: Arc<dyn CommandLaunchDriver>,
    ) -> Self {
        Self {
            workspace,
            config,
            registry: Arc::new(CommandRegistry::new()),
            process_store: Arc::new(process_store),
            launch_driver,
            finalization_options: CommandFinalizationOptions::default(),
        }
    }

    #[must_use]
    pub fn finalization_options(&self) -> &CommandFinalizationOptions {
        &self.finalization_options
    }

    #[must_use]
    pub fn workspace(&self) -> &Arc<WorkspaceManagerService> {
        &self.workspace
    }

    #[must_use]
    pub fn config(&self) -> &::command::CommandConfig {
        &self.config
    }

    #[must_use]
    pub(crate) fn registry(&self) -> &Arc<CommandRegistry> {
        &self.registry
    }

    #[must_use]
    pub(crate) fn process_store(&self) -> &Arc<CommandProcessStore> {
        &self.process_store
    }

    #[must_use]
    pub(crate) fn launch_driver(&self) -> &Arc<dyn CommandLaunchDriver> {
        &self.launch_driver
    }

    pub fn write_stdin(
        &self,
        input: WriteStdinInput,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError> {
        let command_id = input.command_id;
        let yield_time_ms = input
            .yield_time_ms
            .unwrap_or(self.config.default_yield_time_ms);
        let (process, workspace_id) = {
            let active = self.active_for_owner(&command_id, &context.caller_id)?;
            (Arc::clone(&active.process), active.workspace_id.clone())
        };
        if self.workspace().is_remount_pending(&workspace_id) {
            return Err(CommandServiceError::WorkspaceRemountPending { workspace_id });
        }
        let output = {
            process.write_process_stdin(&input.chars).map_err(|error| {
                CommandServiceError::CommandIo {
                    command_id: command_id.clone(),
                    error: error.to_string(),
                }
            })?;
            if yield_time_ms == 0 {
                String::new()
            } else {
                process.read_output_since(0)
            }
        };

        Ok(CommandYield {
            command_id: Some(command_id),
            status: CommandStatus::Running,
            exit_code: None,
            output: CommandOutputSnapshot { stdout: output },
            finalized: None,
        })
    }

    pub fn read_lines(
        &self,
        input: ReadCommandLinesInput,
        context: CommandCallContext,
    ) -> Result<CommandLinesOutput, CommandServiceError> {
        let command_id = input.command_id;
        if let Some(active) = self.active_for_owner_or_none(&command_id, &context.caller_id)? {
            let transcript = active.transcript.clone();
            drop(active);
            return Ok(transcript.window(input.offset, input.limit).into_output(
                command_id,
                CommandStatus::Running,
                None,
            ));
        }

        let completed = self.completed_for_owner(&command_id, &context.caller_id)?;
        Ok(completed
            .transcript
            .window(&command_id, input.offset, input.limit)?
            .into_output(
                command_id,
                completed.result.status,
                completed.result.exit_code,
            ))
    }

    pub fn poll(
        &self,
        input: PollCommandInput,
        context: CommandCallContext,
    ) -> Result<CommandPollOutput, CommandServiceError> {
        let command_id = input.command_id;
        if let Some(active) = self.active_for_owner_or_none(&command_id, &context.caller_id)? {
            if active.process.process_group_id().is_some() {
                if let Some(process_exit) = active.process.take_exit() {
                    drop(active);
                    let result = self.finalize_command(command_id.clone(), process_exit)?;
                    let completed = self.completed_for_owner(&command_id, &context.caller_id)?;
                    let stdout = input.last_n_lines.map_or_else(
                        || result.stdout.clone(),
                        |last_n_lines| ::command::tail_lines(&result.stdout, last_n_lines),
                    );
                    return Ok(CommandPollOutput {
                        command_id,
                        status: result.status,
                        exit_code: result.exit_code,
                        output: CommandOutputSnapshot { stdout },
                        finalized: completed.finalized,
                    });
                }
            }
            let stdout = active
                .process
                .read_recent_output(input.last_n_lines.unwrap_or(200));
            return Ok(CommandPollOutput {
                command_id,
                status: CommandStatus::Running,
                exit_code: None,
                output: CommandOutputSnapshot { stdout },
                finalized: None,
            });
        }

        let completed = self.completed_for_owner(&command_id, &context.caller_id)?;
        let stdout = input.last_n_lines.map_or_else(
            || completed.result.stdout.clone(),
            |last_n_lines| ::command::tail_lines(&completed.result.stdout, last_n_lines),
        );
        Ok(CommandPollOutput {
            command_id,
            status: completed.result.status,
            exit_code: completed.result.exit_code,
            output: CommandOutputSnapshot { stdout },
            finalized: completed.finalized,
        })
    }

    pub fn cancel(
        &self,
        input: CancelCommandInput,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError> {
        let command_id = input.command_id;
        self.ensure_active_owner(&command_id, &context.caller_id)?;
        let output = self
            .process_store
            .update_active(&command_id, |active| {
                if let Some(token) = active.remount_cancellation.clone() {
                    token.request_cancel();
                } else {
                    active.process.cancel_process();
                    active.lifecycle_state = CommandLifecycleState::Cancelled;
                }
                active.cancellation = CancellationState::Requested {
                    requested_at: Instant::now(),
                };
                active.process.read_output_since(0)
            })
            .ok_or_else(|| CommandServiceError::CommandNotFound {
                command_id: command_id.clone(),
            })?;

        Ok(CommandYield {
            command_id: Some(command_id),
            status: CommandStatus::Running,
            exit_code: None,
            output: CommandOutputSnapshot { stdout: output },
            finalized: None,
        })
    }

    fn active_for_owner<'a>(
        &'a self,
        command_id: &crate::command::CommandId,
        caller_id: &CallerId,
    ) -> Result<crate::command::ActiveCommandRef<'a>, CommandServiceError> {
        match self.active_for_owner_or_none(command_id, caller_id)? {
            Some(active) => Ok(active),
            None => match self.process_store.completed(command_id) {
                Some(completed) if completed.caller_id == *caller_id => {
                    Err(CommandServiceError::CommandAlreadyCompleted {
                        command_id: command_id.clone(),
                    })
                }
                Some(completed) => Err(CommandServiceError::CommandCallerMismatch {
                    command_id: command_id.clone(),
                    expected: completed.caller_id,
                    actual: caller_id.clone(),
                }),
                None => Err(CommandServiceError::CommandNotFound {
                    command_id: command_id.clone(),
                }),
            },
        }
    }

    fn active_for_owner_or_none<'a>(
        &'a self,
        command_id: &crate::command::CommandId,
        caller_id: &CallerId,
    ) -> Result<Option<crate::command::ActiveCommandRef<'a>>, CommandServiceError> {
        let Some(active) = self.process_store.active(command_id) else {
            return Ok(None);
        };
        if active.caller_id != *caller_id {
            return Err(CommandServiceError::CommandCallerMismatch {
                command_id: command_id.clone(),
                expected: active.caller_id.clone(),
                actual: caller_id.clone(),
            });
        }
        if let FinalizationState::Failed { error, finalized } = &active.finalization {
            return Err(CommandServiceError::CommandFinalizationFailed {
                command_id: command_id.clone(),
                error: error.clone(),
                finalized: finalized.clone().map(Box::new),
            });
        }
        Ok(Some(active))
    }

    fn completed_for_owner(
        &self,
        command_id: &crate::command::CommandId,
        caller_id: &CallerId,
    ) -> Result<CompletedCommandRecord, CommandServiceError> {
        let completed = self.process_store.completed(command_id).ok_or_else(|| {
            CommandServiceError::CommandNotFound {
                command_id: command_id.clone(),
            }
        })?;
        if completed.caller_id == *caller_id {
            Ok(completed)
        } else {
            Err(CommandServiceError::CommandCallerMismatch {
                command_id: command_id.clone(),
                expected: completed.caller_id,
                actual: caller_id.clone(),
            })
        }
    }

    fn ensure_active_owner(
        &self,
        command_id: &crate::command::CommandId,
        caller_id: &CallerId,
    ) -> Result<(), CommandServiceError> {
        drop(self.active_for_owner(command_id, caller_id)?);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::Arc;
    use std::time::Instant;

    use crate::command::{
        ActiveCommandProcess, CancellationState, CommandCallContext, CommandFinalizePolicy,
        CommandFinalizedMetadata, CommandId, CommandLifecycleState, CommandProcessStore,
        CommandServiceError, CommandStatus, CommandStream, CommandTerminalResult,
        CommandTraceOrigin, CommandTranscriptRow, CommandTranscriptStore, CompletedCommandRecord,
        FinalizationState, OperationTraceContext, PollCommandInput, ReadCommandLinesInput,
        RetainedCommandTranscript, WriteStdinInput,
    };
    use crate::workspace_crate::{
        CallerId, CaptureChangesRequest, CapturedWorkspaceChanges, CreateWorkspaceRequest,
        DestroyWorkspaceRequest, DestroyWorkspaceResult, LatestSnapshotRequest,
        ReadonlySnapshotHandle, RemountWorkspaceRequest, RemountWorkspaceResult, WorkspaceError,
        WorkspaceHandle, WorkspaceId, WorkspaceService,
    };
    use crate::workspace_manager::WorkspaceManagerService;

    use super::CommandOperationService;

    struct NoopWorkspaceService;

    impl WorkspaceService for NoopWorkspaceService {
        fn create_workspace(
            &self,
            _request: CreateWorkspaceRequest,
        ) -> Result<WorkspaceHandle, WorkspaceError> {
            Err(WorkspaceError::Setup {
                step: "not configured".to_owned(),
            })
        }

        fn capture_changes(
            &self,
            _handle: &WorkspaceHandle,
            _request: CaptureChangesRequest,
        ) -> Result<CapturedWorkspaceChanges, WorkspaceError> {
            Err(WorkspaceError::Capture {
                message: "not configured".to_owned(),
            })
        }

        fn remount_workspace(
            &self,
            _handle: &WorkspaceHandle,
            _request: RemountWorkspaceRequest,
        ) -> Result<RemountWorkspaceResult, WorkspaceError> {
            Err(WorkspaceError::Setup {
                step: "not configured".to_owned(),
            })
        }

        fn destroy_workspace(
            &self,
            _handle: WorkspaceHandle,
            _request: DestroyWorkspaceRequest,
        ) -> Result<DestroyWorkspaceResult, WorkspaceError> {
            Err(WorkspaceError::Setup {
                step: "not configured".to_owned(),
            })
        }

        fn latest_snapshot(
            &self,
            _request: LatestSnapshotRequest,
        ) -> Result<ReadonlySnapshotHandle, WorkspaceError> {
            Err(WorkspaceError::SnapshotAcquire {
                source: "not configured".to_owned(),
            })
        }
    }

    fn command_service() -> CommandOperationService {
        let workspace = Arc::new(WorkspaceManagerService::new(Arc::new(NoopWorkspaceService)));
        CommandOperationService::with_process_store_for_test(
            workspace,
            command::CommandConfig::default(),
            CommandProcessStore::new(),
        )
    }

    fn command_id(id: &str) -> CommandId {
        CommandId(id.to_owned())
    }

    fn caller_id(id: &str) -> CallerId {
        CallerId(id.to_owned())
    }

    fn workspace_id(id: &str) -> WorkspaceId {
        WorkspaceId(id.to_owned())
    }

    fn context(caller_id: &str) -> CommandCallContext {
        CommandCallContext {
            caller_id: CallerId(caller_id.to_owned()),
            trace: OperationTraceContext,
        }
    }

    fn inactive_process(command_id: &CommandId, caller_id: &CallerId) -> command::CommandProcess {
        command::CommandProcess::inactive_for_test(command::CommandProcessSpec {
            id: command_id.0.clone(),
            caller_id: caller_id.0.clone(),
            command: "cat".to_owned(),
            timeout_seconds: None,
        })
    }

    fn active_record(
        command_id: CommandId,
        caller_id: CallerId,
        workspace_id: WorkspaceId,
    ) -> ActiveCommandProcess {
        ActiveCommandProcess {
            command_id: command_id.clone(),
            caller_id: caller_id.clone(),
            workspace_id: workspace_id.clone(),
            workspace_root: PathBuf::from("/workspace"),
            process: Arc::new(inactive_process(&command_id, &caller_id)),
            transcript: CommandTranscriptStore {
                transcript_path: Some(write_transcript(&command_id, "active", "active output\n")),
            },
            finalize_policy: CommandFinalizePolicy::Session { workspace_id },
            lifecycle_state: CommandLifecycleState::Running,
            cancellation: CancellationState::None,
            remount_cancellation: None,
            remount_switch_state: None,
            finalization: FinalizationState::NotStarted,
            trace_origin: CommandTraceOrigin,
            started_at: Instant::now(),
        }
    }

    fn completed_record(
        command_id: CommandId,
        caller_id: CallerId,
        workspace_id: WorkspaceId,
        stdout: &str,
    ) -> CompletedCommandRecord {
        CompletedCommandRecord {
            command_id: command_id.clone(),
            caller_id,
            workspace_id,
            result: CommandTerminalResult {
                status: CommandStatus::Completed,
                exit_code: Some(0),
                stdout: stdout.to_owned(),
            },
            transcript: RetainedCommandTranscript {
                transcript_path: Some(write_transcript(&command_id, "completed", stdout)),
            },
            finalization: FinalizationState::Complete,
            finalized: Some(CommandFinalizedMetadata::default()),
            completed_at: Instant::now(),
        }
    }

    fn write_transcript(command_id: &CommandId, suffix: &str, text: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "operation-service-{suffix}-{}-{}-{}.log",
            std::process::id(),
            unique_suffix(),
            command_id.0
        ));
        std::fs::write(&path, text).expect("test transcript write succeeds");
        path
    }

    fn unique_suffix() -> u64 {
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        COUNTER.fetch_add(1, Ordering::Relaxed)
    }

    fn seed_active(
        service: &CommandOperationService,
        command_id: CommandId,
        caller_id: CallerId,
        workspace_id: WorkspaceId,
    ) {
        service
            .registry()
            .bind(command_id.clone(), workspace_id.clone())
            .expect("registry bind succeeds");
        let reservation = service
            .process_store()
            .try_reserve()
            .expect("reservation succeeds");
        service
            .process_store()
            .insert_active(
                reservation,
                active_record(command_id, caller_id, workspace_id),
            )
            .expect("active insert succeeds");
    }

    fn complete_seeded_active(
        service: &CommandOperationService,
        command_id: CommandId,
        caller_id: CallerId,
        workspace_id: WorkspaceId,
        stdout: &str,
    ) {
        let record = completed_record(command_id.clone(), caller_id, workspace_id, stdout);
        service
            .process_store()
            .complete_active(record)
            .expect("completion succeeds")
            .expect("active record is removed");
        let _ = service.registry().unbind(&command_id);
    }

    #[test]
    fn command_ownership_allows_completed_read_for_owner_only() {
        let service = command_service();
        let command_id = command_id("cmd_completed");
        let owner = caller_id("caller-owner");
        let workspace_id = workspace_id("workspace-1");
        seed_active(
            &service,
            command_id.clone(),
            owner.clone(),
            workspace_id.clone(),
        );
        complete_seeded_active(
            &service,
            command_id.clone(),
            owner,
            workspace_id,
            "first\nsecond\nthird\n",
        );

        let output = service
            .read_lines(
                ReadCommandLinesInput {
                    command_id: command_id.clone(),
                    offset: 1,
                    limit: 1,
                },
                context("caller-owner"),
            )
            .expect("owner can read completed command output");

        assert_eq!(output.total_lines, 3);
        assert_eq!(output.status, CommandStatus::Completed);
        assert_eq!(output.exit_code, Some(0));
        assert_eq!(output.truncated_before, 0);
        assert_eq!(output.next_offset, 2);
        assert!(output.output_truncated);
        assert_eq!(
            output.output,
            vec![CommandTranscriptRow {
                offset: 1,
                stream: CommandStream::Stdout,
                text: "second".to_owned()
            }]
        );

        let error = service
            .read_lines(
                ReadCommandLinesInput {
                    command_id: command_id.clone(),
                    offset: 0,
                    limit: 1,
                },
                context("caller-other"),
            )
            .expect_err("wrong caller cannot read completed command output");
        assert!(matches!(
            error,
            CommandServiceError::CommandCallerMismatch { command_id: id, expected, actual }
                if id == command_id
                    && expected == caller_id("caller-owner")
                    && actual == caller_id("caller-other")
        ));
    }

    #[test]
    fn command_ownership_rejects_wrong_caller_for_completed_poll_stdin_and_cancel() {
        let service = command_service();
        let command_id = command_id("cmd_completed");
        let owner = caller_id("caller-owner");
        let workspace_id = workspace_id("workspace-1");
        seed_active(
            &service,
            command_id.clone(),
            owner.clone(),
            workspace_id.clone(),
        );
        complete_seeded_active(&service, command_id.clone(), owner, workspace_id, "done\n");

        let poll_error = service
            .poll(
                PollCommandInput {
                    command_id: command_id.clone(),
                    last_n_lines: Some(1),
                },
                context("caller-other"),
            )
            .expect_err("wrong caller cannot poll completed command");
        let stdin_error = service
            .write_stdin(
                WriteStdinInput {
                    command_id: command_id.clone(),
                    chars: "ignored".to_owned(),
                    yield_time_ms: Some(0),
                },
                context("caller-other"),
            )
            .expect_err("wrong caller cannot write stdin to completed command");
        let cancel_error = service
            .cancel(
                crate::command::CancelCommandInput {
                    command_id: command_id.clone(),
                },
                context("caller-other"),
            )
            .expect_err("wrong caller cannot cancel completed command");

        for error in [poll_error, stdin_error, cancel_error] {
            assert!(matches!(
                error,
                CommandServiceError::CommandCallerMismatch { command_id: id, expected, actual }
                    if id == command_id
                        && expected == caller_id("caller-owner")
                        && actual == caller_id("caller-other")
            ));
        }
    }
}
