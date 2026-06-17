use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;

use operation_service::command::{
    ActiveCommandProcess, CancelCommandInput, CancellationState, CommandCallContext,
    CommandFinalizePolicy, CommandId, CommandLifecycleState, CommandOperationService,
    CommandOutputLine, CommandProcessStore, CommandServiceError, CommandStatus,
    CommandTerminalResult, CommandTraceOrigin, CommandTranscriptStore, CompletedCommandRecord,
    FinalizationState, OperationTraceContext, PollCommandInput, ReadCommandLinesInput,
    RetainedCommandTranscript, WriteStdinInput,
};
use operation_service::workspace_manager::WorkspaceManagerService;
use workspace::{
    CallerId, CaptureChangesRequest, CapturedWorkspaceChanges, CreateWorkspaceRequest,
    DestroyWorkspaceRequest, DestroyWorkspaceResult, LatestSnapshotRequest, ReadonlySnapshotHandle,
    RemountWorkspaceRequest, RemountWorkspaceResult, WorkspaceError, WorkspaceHandle, WorkspaceId,
    WorkspaceService,
};

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

fn command_service() -> Arc<CommandOperationService> {
    let workspace = Arc::new(WorkspaceManagerService::new(Arc::new(NoopWorkspaceService)));
    Arc::new(CommandOperationService::new(
        workspace,
        command::CommandConfig::default(),
    ))
}

fn inactive_process(command_id: &CommandId, caller_id: &CallerId) -> command::CommandProcess {
    command::CommandProcess::new(command::CommandProcessSpec {
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
        process: inactive_process(&command_id, &caller_id),
        transcript: CommandTranscriptStore {
            transcript_path: Some(PathBuf::from("/tmp/transcript.jsonl")),
        },
        finalize_policy: CommandFinalizePolicy::Session { workspace_id },
        lifecycle_state: CommandLifecycleState::Running,
        cancellation: CancellationState::None,
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
        command_id,
        caller_id,
        workspace_id,
        result: CommandTerminalResult {
            status: CommandStatus::Completed,
            exit_code: Some(0),
            stdout: stdout.to_owned(),
        },
        transcript: RetainedCommandTranscript {
            transcript_path: Some(PathBuf::from("/tmp/retained-transcript.jsonl")),
        },
        finalization: FinalizationState::Complete,
        completed_at: Instant::now(),
    }
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
    store: &CommandProcessStore,
    command_id: CommandId,
    caller_id: CallerId,
    workspace_id: WorkspaceId,
    stdout: &str,
) {
    store
        .complete_active(completed_record(
            command_id,
            caller_id,
            workspace_id,
            stdout,
        ))
        .expect("completion succeeds")
        .expect("active record is removed");
}

#[test]
fn command_ownership_rejects_wrong_caller_for_active_poll() {
    let service = command_service();
    let command_id = command_id("cmd_active");
    seed_active(
        &service,
        command_id.clone(),
        caller_id("caller-owner"),
        workspace_id("workspace-1"),
    );

    let error = service
        .poll(
            PollCommandInput {
                command_id: command_id.clone(),
                last_n_lines: Some(10),
            },
            context("caller-other"),
        )
        .expect_err("wrong caller is rejected");

    assert!(matches!(
        error,
        CommandServiceError::CommandCallerMismatch { command_id: id, expected, actual }
            if id == command_id
                && expected == caller_id("caller-owner")
                && actual == caller_id("caller-other")
    ));
}

#[test]
fn command_ownership_validates_stdin_against_active_owner() {
    let service = command_service();
    let command_id = command_id("cmd_stdin");
    seed_active(
        &service,
        command_id.clone(),
        caller_id("caller-owner"),
        workspace_id("workspace-1"),
    );

    let error = service
        .write_stdin(
            WriteStdinInput {
                command_id: command_id.clone(),
                chars: "hello\n".to_owned(),
                yield_time_ms: Some(0),
            },
            context("caller-other"),
        )
        .expect_err("wrong caller cannot write stdin");
    assert!(matches!(
        error,
        CommandServiceError::CommandCallerMismatch { command_id: id, expected, actual }
            if id == command_id
                && expected == caller_id("caller-owner")
                && actual == caller_id("caller-other")
    ));

    let output = service
        .write_stdin(
            WriteStdinInput {
                command_id: command_id.clone(),
                chars: "hello\n".to_owned(),
                yield_time_ms: Some(0),
            },
            context("caller-owner"),
        )
        .expect("owner can write stdin");
    assert_eq!(output.status, CommandStatus::Running);
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
        service.process_store(),
        command_id.clone(),
        owner.clone(),
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
    assert_eq!(output.next_offset, 2);
    assert!(output.output_truncated);
    assert_eq!(
        output.output,
        vec![CommandOutputLine {
            offset: 1,
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
fn command_ownership_cancel_marks_active_command_for_owner() {
    let service = command_service();
    let command_id = command_id("cmd_cancel");
    seed_active(
        &service,
        command_id.clone(),
        caller_id("caller-owner"),
        workspace_id("workspace-1"),
    );

    let output = service
        .cancel(
            CancelCommandInput {
                command_id: command_id.clone(),
            },
            context("caller-owner"),
        )
        .expect("owner can cancel active command");

    assert_eq!(output.status, CommandStatus::Running);
    let active = service
        .process_store()
        .active(&command_id)
        .expect("cancelled command remains active until finalization");
    assert_eq!(active.lifecycle_state, CommandLifecycleState::Cancelled);
    assert!(matches!(
        active.cancellation,
        CancellationState::Requested { .. }
    ));
}
