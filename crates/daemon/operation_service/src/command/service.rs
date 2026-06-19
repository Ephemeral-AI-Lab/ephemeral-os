use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use crate::command::{
    CommandLaunchDriver, CommandProcessStore, CommandRegistry, CommandServiceError,
    CompletedCommandRecord, FinalizationState, RealCommandLaunchDriver,
};
use crate::workspace_crate::CallerId;
use crate::workspace_remount::command_quiesce::ProcProcessGroupController;
use crate::workspace_remount::ProcessGroupController;
use crate::workspace_session::WorkspaceSessionService;

mod impls;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct CommandFinalizationOptions {
    pub one_shot_capture: layerstack::service::BoundedCaptureOptions,
    pub one_shot_publish: layerstack::CommitOptions,
}

pub struct CommandOperationService {
    workspace: Arc<WorkspaceSessionService>,
    config: ::command::CommandConfig,
    registry: Arc<CommandRegistry>,
    process_store: Arc<CommandProcessStore>,
    launch_driver: Arc<dyn CommandLaunchDriver>,
    remount_controller: Arc<dyn ProcessGroupController>,
    remount_admission: Mutex<()>,
    finalization_options: CommandFinalizationOptions,
}

impl CommandOperationService {
    #[must_use]
    pub fn new(workspace: Arc<WorkspaceSessionService>, config: ::command::CommandConfig) -> Self {
        Self::with_finalization_options(workspace, config, CommandFinalizationOptions::default())
    }

    #[must_use]
    pub fn with_finalization_options(
        workspace: Arc<WorkspaceSessionService>,
        config: ::command::CommandConfig,
        finalization_options: CommandFinalizationOptions,
    ) -> Self {
        Self {
            workspace,
            config,
            registry: Arc::new(CommandRegistry::new()),
            process_store: Arc::new(CommandProcessStore::new()),
            launch_driver: Arc::new(RealCommandLaunchDriver),
            remount_controller: Arc::new(ProcProcessGroupController),
            remount_admission: Mutex::new(()),
            finalization_options,
        }
    }

    #[doc(hidden)]
    #[must_use]
    pub fn with_launch_driver_for_test(
        workspace: Arc<WorkspaceSessionService>,
        config: ::command::CommandConfig,
        launch_driver: Arc<dyn CommandLaunchDriver>,
    ) -> Self {
        Self {
            workspace,
            config,
            registry: Arc::new(CommandRegistry::new()),
            process_store: Arc::new(CommandProcessStore::new()),
            launch_driver,
            remount_controller: Arc::new(ProcProcessGroupController),
            remount_admission: Mutex::new(()),
            finalization_options: CommandFinalizationOptions::default(),
        }
    }

    #[doc(hidden)]
    #[must_use]
    pub fn with_launch_driver_and_remount_controller_for_test(
        workspace: Arc<WorkspaceSessionService>,
        config: ::command::CommandConfig,
        launch_driver: Arc<dyn CommandLaunchDriver>,
        remount_controller: Arc<dyn ProcessGroupController>,
    ) -> Self {
        Self {
            workspace,
            config,
            registry: Arc::new(CommandRegistry::new()),
            process_store: Arc::new(CommandProcessStore::new()),
            launch_driver,
            remount_controller,
            remount_admission: Mutex::new(()),
            finalization_options: CommandFinalizationOptions::default(),
        }
    }

    #[cfg(test)]
    pub(crate) fn with_process_store_for_test(
        workspace: Arc<WorkspaceSessionService>,
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
        workspace: Arc<WorkspaceSessionService>,
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
            remount_controller: Arc::new(ProcProcessGroupController),
            remount_admission: Mutex::new(()),
            finalization_options: CommandFinalizationOptions::default(),
        }
    }

    #[must_use]
    pub fn finalization_options(&self) -> &CommandFinalizationOptions {
        &self.finalization_options
    }

    #[must_use]
    pub fn workspace(&self) -> &Arc<WorkspaceSessionService> {
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

    #[must_use]
    pub(crate) fn remount_controller(&self) -> Arc<dyn ProcessGroupController> {
        Arc::clone(&self.remount_controller)
    }

    pub(crate) fn lock_remount_admission(&self) -> MutexGuard<'_, ()> {
        self.remount_admission
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
    }

    pub(crate) fn active_for_owner<'a>(
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

    pub(crate) fn active_for_owner_or_none<'a>(
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

    pub(crate) fn completed_for_owner(
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

    pub(crate) fn ensure_active_owner(
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
        CallerId, CaptureChangesRequest, CreateWorkspaceRequest, DestroyWorkspaceRequest,
        LatestSnapshotRequest, RemountWorkspaceRequest, WorkspaceError, WorkspaceHandle,
        WorkspaceId, WorkspaceRuntimeHooks, WorkspaceRuntimeService,
    };
    use crate::workspace_session::WorkspaceSessionService;

    use super::CommandOperationService;

    fn command_service() -> CommandOperationService {
        let workspace = Arc::new(WorkspaceSessionService::new(noop_workspace_runtime()));
        CommandOperationService::with_process_store_for_test(
            workspace,
            command::CommandConfig::default(),
            CommandProcessStore::new(),
        )
    }

    fn noop_workspace_runtime() -> Arc<WorkspaceRuntimeService> {
        Arc::new(WorkspaceRuntimeService::from_hooks_for_test(
            WorkspaceRuntimeHooks {
                create_workspace: Box::new(|_request: CreateWorkspaceRequest| {
                    Err(WorkspaceError::Setup {
                        step: "not configured".to_owned(),
                    })
                }),
                capture_changes: Box::new(
                    |_handle: &WorkspaceHandle, _request: CaptureChangesRequest| {
                        Err(WorkspaceError::Capture {
                            message: "not configured".to_owned(),
                        })
                    },
                ),
                remount_workspace: Box::new(
                    |_handle: &WorkspaceHandle, _request: RemountWorkspaceRequest| {
                        Err(WorkspaceError::Setup {
                            step: "not configured".to_owned(),
                        })
                    },
                ),
                destroy_workspace: Box::new(
                    |_handle: WorkspaceHandle, _request: DestroyWorkspaceRequest| {
                        Err(WorkspaceError::Setup {
                            step: "not configured".to_owned(),
                        })
                    },
                ),
                latest_snapshot: Box::new(|_request: LatestSnapshotRequest| {
                    Err(WorkspaceError::SnapshotAcquire {
                        source: "not configured".to_owned(),
                    })
                }),
            },
        ))
    }

    fn command_id(id: &str) -> CommandId {
        CommandId(id.to_owned())
    }

    fn caller_id(id: &str) -> CallerId {
        CallerId(id.to_owned())
    }

    fn workspace_session_id(id: &str) -> WorkspaceId {
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
            cwd: None,
            timeout_seconds: None,
        })
    }

    fn active_record(
        command_id: CommandId,
        caller_id: CallerId,
        workspace_session_id: WorkspaceId,
    ) -> ActiveCommandProcess {
        ActiveCommandProcess {
            command_id: command_id.clone(),
            caller_id: caller_id.clone(),
            workspace_session_id: workspace_session_id.clone(),
            workspace_root: PathBuf::from("/workspace"),
            process: Arc::new(inactive_process(&command_id, &caller_id)),
            transcript: CommandTranscriptStore {
                transcript_path: Some(write_transcript(&command_id, "active", "active output\n")),
            },
            finalize_policy: CommandFinalizePolicy::Session {
                workspace_session_id,
            },
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
        workspace_session_id: WorkspaceId,
        stdout: &str,
    ) -> CompletedCommandRecord {
        CompletedCommandRecord {
            command_id: command_id.clone(),
            caller_id,
            workspace_session_id,
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
        workspace_session_id: WorkspaceId,
    ) {
        service
            .registry()
            .bind(command_id.clone(), workspace_session_id.clone())
            .expect("registry bind succeeds");
        let reservation = service
            .process_store()
            .try_reserve()
            .expect("reservation succeeds");
        service
            .process_store()
            .insert_active(
                reservation,
                active_record(command_id, caller_id, workspace_session_id),
            )
            .expect("active insert succeeds");
    }

    fn complete_seeded_active(
        service: &CommandOperationService,
        command_id: CommandId,
        caller_id: CallerId,
        workspace_session_id: WorkspaceId,
        stdout: &str,
    ) {
        let record = completed_record(command_id.clone(), caller_id, workspace_session_id, stdout);
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
        let workspace_session_id = workspace_session_id("workspace-1");
        seed_active(
            &service,
            command_id.clone(),
            owner.clone(),
            workspace_session_id.clone(),
        );
        complete_seeded_active(
            &service,
            command_id.clone(),
            owner,
            workspace_session_id,
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
        let workspace_session_id = workspace_session_id("workspace-1");
        seed_active(
            &service,
            command_id.clone(),
            owner.clone(),
            workspace_session_id.clone(),
        );
        complete_seeded_active(
            &service,
            command_id.clone(),
            owner,
            workspace_session_id,
            "done\n",
        );

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
