use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use command::process::{
    CommandProcess, CommandProcessExit, CommandProcessSpawn, CommandProcessSpec,
};
use command::yield_wait_loop::WaitOutcome;
use operation_service::command::{
    CommandCallContext, CommandLaunchDriver, CommandOperationService, CommandServiceError,
    ExecCommandInput, OperationTraceContext, PollCommandInput, ReadCommandLinesInput,
    WriteStdinInput,
};
use operation_service::workspace_manager::{WorkspaceManagerService, WorkspaceRemountState};
use operation_service::workspace_remount::{WorkspaceRemountOptions, WorkspaceRemountService};
use operation_service::OperationServices;
use workspace::{
    BaseRevision, CallerId, CaptureChangesRequest, CapturedWorkspaceChanges,
    CreateWorkspaceRequest, DestroyWorkspaceRequest, DestroyWorkspaceResult, LatestSnapshotRequest,
    LayerStackSnapshotRef, LeaseId, NetworkMode, ReadonlySnapshotHandle, RemountWorkspaceRequest,
    RemountWorkspaceResult, WorkspaceError, WorkspaceHandle, WorkspaceId, WorkspaceLaunchContext,
    WorkspaceService,
};

struct TestServices {
    workspace: Arc<WorkspaceManagerService>,
    command: Arc<CommandOperationService>,
    services: OperationServices,
}

#[derive(Default)]
struct PendingGuardWorkspaceService {
    create_results: Mutex<VecDeque<Result<WorkspaceHandle, WorkspaceError>>>,
}

impl PendingGuardWorkspaceService {
    fn push_create_result(&self, result: Result<WorkspaceHandle, WorkspaceError>) {
        self.create_results
            .lock()
            .expect("test operation succeeds")
            .push_back(result);
    }
}

impl WorkspaceService for PendingGuardWorkspaceService {
    fn create_workspace(
        &self,
        _request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceHandle, WorkspaceError> {
        self.create_results
            .lock()
            .expect("test operation succeeds")
            .pop_front()
            .unwrap_or_else(|| {
                Err(WorkspaceError::Setup {
                    step: "create result not configured".to_owned(),
                })
            })
    }

    fn capture_changes(
        &self,
        _handle: &WorkspaceHandle,
        _request: CaptureChangesRequest,
    ) -> Result<CapturedWorkspaceChanges, WorkspaceError> {
        Err(WorkspaceError::Capture {
            message: "capture result not configured".to_owned(),
        })
    }

    fn remount_workspace(
        &self,
        _handle: &WorkspaceHandle,
        _request: RemountWorkspaceRequest,
    ) -> Result<RemountWorkspaceResult, WorkspaceError> {
        Err(WorkspaceError::Setup {
            step: "remount result not configured".to_owned(),
        })
    }

    fn destroy_workspace(
        &self,
        handle: WorkspaceHandle,
        _request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceError> {
        Ok(DestroyWorkspaceResult {
            workspace_id: handle.id,
            owner: handle.owner,
            evicted_upperdir_bytes: 0,
            lifetime_s: 0.0,
            lease_released: Some(true),
            lease_release_error: None,
            active_leases_after: 0,
        })
    }

    fn latest_snapshot(
        &self,
        _request: LatestSnapshotRequest,
    ) -> Result<ReadonlySnapshotHandle, WorkspaceError> {
        Err(WorkspaceError::SnapshotAcquire {
            source: "latest snapshot not configured".to_owned(),
        })
    }
}

struct PendingGuardLaunchDriver;

impl CommandLaunchDriver for PendingGuardLaunchDriver {
    fn spawn(
        &self,
        spec: CommandProcessSpec,
        _parts: CommandProcessSpawn<'_>,
    ) -> Result<CommandProcess, CommandServiceError> {
        Ok(CommandProcess::inactive_for_test(spec))
    }

    fn wait_for_initial_yield(
        &self,
        _process: &CommandProcess,
        _config: &command::CommandConfig,
        _yield_time_ms: u64,
        _start_offset: u64,
    ) -> WaitOutcome<CommandProcessExit> {
        WaitOutcome::Running(String::new())
    }
}

fn build_services(fake: Arc<PendingGuardWorkspaceService>) -> TestServices {
    let workspace = Arc::new(WorkspaceManagerService::new(fake));
    let command = Arc::new(CommandOperationService::with_launch_driver_for_test(
        Arc::clone(&workspace),
        command_config(),
        Arc::new(PendingGuardLaunchDriver),
    ));
    let remount = Arc::new(WorkspaceRemountService::new(
        Arc::clone(&workspace),
        Arc::clone(&command),
        WorkspaceRemountOptions::default(),
    ));
    let services = OperationServices::new(Arc::clone(&workspace), Arc::clone(&command), remount);
    TestServices {
        workspace,
        command,
        services,
    }
}

fn create_request(caller_id: &str, workspace_root: PathBuf) -> CreateWorkspaceRequest {
    CreateWorkspaceRequest {
        caller_id: CallerId(caller_id.to_owned()),
        workspace_root,
        layer_stack_root: PathBuf::from("/layers"),
        network: NetworkMode::Host,
    }
}

fn exec_input(workspace_id: WorkspaceId, workspace_root: PathBuf) -> ExecCommandInput {
    ExecCommandInput {
        caller_id: CallerId("caller-1".to_owned()),
        workspace_root,
        workspace_id: Some(workspace_id),
        cmd: "echo ok".to_owned(),
        cwd: None,
        timeout_seconds: None,
        yield_time_ms: Some(0),
    }
}

fn context(caller_id: &str) -> CommandCallContext {
    CommandCallContext {
        caller_id: CallerId(caller_id.to_owned()),
        trace: OperationTraceContext,
    }
}

fn workspace_handle(
    workspace_id: &str,
    caller_id: &str,
    lease_id: &str,
    workspace_root: PathBuf,
) -> WorkspaceHandle {
    let snapshot = LayerStackSnapshotRef {
        lease_id: LeaseId(lease_id.to_owned()),
        manifest_version: 1,
        root_hash: "root".to_owned(),
        layer_paths: vec![PathBuf::from("/lower/one")],
    };
    WorkspaceHandle {
        id: WorkspaceId(workspace_id.to_owned()),
        owner: CallerId(caller_id.to_owned()),
        workspace_root,
        network: NetworkMode::Host,
        base_revision: BaseRevision {
            version: 1,
            root_hash: "root".to_owned(),
            layer_count: 1,
        },
        snapshot,
        launch: Some(WorkspaceLaunchContext {
            upperdir: PathBuf::from("/tmp/command-remount-upper"),
            workdir: PathBuf::from("/tmp/command-remount-work"),
            namespace_fds: None,
            cgroup_path: None,
        }),
    }
}

fn create_session_and_command() -> (
    TestServices,
    WorkspaceId,
    operation_service::command::CommandId,
) {
    let fake = Arc::new(PendingGuardWorkspaceService::default());
    let services = build_services(Arc::clone(&fake));
    let workspace_root = PathBuf::from("/workspace");
    fake.push_create_result(Ok(workspace_handle(
        "workspace-1",
        "caller-1",
        "lease-1",
        workspace_root.clone(),
    )));
    let handler = services
        .workspace
        .create(create_request("caller-1", workspace_root.clone()))
        .expect("create workspace session succeeds");
    let output = services
        .services
        .exec_command(
            exec_input(handler.workspace_id.clone(), workspace_root),
            OperationTraceContext,
        )
        .expect("exec command succeeds");
    (
        services,
        handler.workspace_id,
        output.command_id.expect("running command has id"),
    )
}

fn command_config() -> command::CommandConfig {
    command::CommandConfig {
        scratch_root: std::env::temp_dir().join(format!(
            "operation-service-command-remount-test-{}-{}",
            std::process::id(),
            unique_suffix()
        )),
        ..command::CommandConfig::default()
    }
}

fn unique_suffix() -> u64 {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    COUNTER.fetch_add(1, Ordering::Relaxed)
}

#[test]
fn command_remount_start_rejects_for_pending_persistent_workspace() {
    let fake = Arc::new(PendingGuardWorkspaceService::default());
    let services = build_services(Arc::clone(&fake));
    let workspace_root = PathBuf::from("/workspace");
    fake.push_create_result(Ok(workspace_handle(
        "workspace-1",
        "caller-1",
        "lease-1",
        workspace_root.clone(),
    )));
    let handler = services
        .workspace
        .create(create_request("caller-1", workspace_root.clone()))
        .expect("create workspace session succeeds");
    services
        .workspace
        .begin_remount(handler.workspace_id.clone())
        .expect("begin remount succeeds");

    let error = services
        .services
        .exec_command(
            exec_input(handler.workspace_id.clone(), workspace_root),
            OperationTraceContext,
        )
        .expect_err("exec rejects pending remount");

    assert!(matches!(
        error,
        CommandServiceError::WorkspaceRemountPending { workspace_id }
            if workspace_id == WorkspaceId("workspace-1".to_owned())
    ));
}

#[test]
fn command_remount_stdin_rejects_for_active_command_when_workspace_becomes_pending() {
    let (services, workspace_id, command_id) = create_session_and_command();
    services
        .workspace
        .begin_remount(workspace_id.clone())
        .expect("begin remount succeeds");

    let error = services
        .command
        .write_stdin(
            WriteStdinInput {
                command_id: command_id.clone(),
                chars: "input".to_owned(),
                yield_time_ms: Some(0),
            },
            context("caller-1"),
        )
        .expect_err("stdin rejects pending remount");

    assert!(matches!(
        error,
        CommandServiceError::WorkspaceRemountPending { workspace_id: pending }
            if pending == workspace_id
    ));
}

#[test]
fn command_remount_read_lines_and_poll_remain_allowed_while_pending() {
    let (services, workspace_id, command_id) = create_session_and_command();
    services
        .workspace
        .begin_remount(workspace_id)
        .expect("begin remount succeeds");

    let rows = services
        .command
        .read_lines(
            ReadCommandLinesInput {
                command_id: command_id.clone(),
                offset: 0,
                limit: 10,
            },
            context("caller-1"),
        )
        .expect("read lines remains allowed");
    let poll = services
        .command
        .poll(
            PollCommandInput {
                command_id,
                last_n_lines: Some(5),
            },
            context("caller-1"),
        )
        .expect("poll remains allowed");

    assert_eq!(
        rows.status,
        operation_service::command::CommandStatus::Running
    );
    assert_eq!(
        poll.status,
        operation_service::command::CommandStatus::Running
    );
}

#[test]
fn command_remount_wrong_caller_does_not_observe_pending_state() {
    let (services, workspace_id, command_id) = create_session_and_command();
    services
        .workspace
        .begin_remount(workspace_id)
        .expect("begin remount succeeds");

    let error = services
        .command
        .write_stdin(
            WriteStdinInput {
                command_id: command_id.clone(),
                chars: "input".to_owned(),
                yield_time_ms: Some(0),
            },
            context("caller-2"),
        )
        .expect_err("wrong caller remains authorization failure");

    assert!(matches!(
        error,
        CommandServiceError::CommandCallerMismatch { command_id: id, .. } if id == command_id
    ));
    assert_eq!(
        services
            .workspace
            .remount_state(&WorkspaceId("workspace-1".to_owned()))
            .expect("remount state is readable"),
        WorkspaceRemountState::RemountPending
    );
}
