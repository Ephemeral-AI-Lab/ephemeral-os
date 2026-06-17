use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use command::process::{
    CommandProcess, CommandProcessExit, CommandProcessSpawn, CommandProcessSpec,
};
use command::yield_wait_loop::WaitOutcome;
use operation_service::command::{
    CancelCommandInput, CommandCallContext, CommandId, CommandLaunchDriver, CommandServiceError,
    CommandStatus, ExecCommandInput, OperationTraceContext, PollCommandInput,
    ReadCommandLinesInput, WriteStdinInput,
};
use operation_service::workspace_manager::WorkspaceManagerService;
use operation_service::workspace_remount::{WorkspaceRemountOptions, WorkspaceRemountService};
use operation_service::OperationServices;
use workspace::{
    BaseRevision, CallerId, CaptureChangesRequest, CapturedWorkspaceChanges,
    CreateWorkspaceRequest, DestroyWorkspaceRequest, DestroyWorkspaceResult, LatestSnapshotRequest,
    LayerStackSnapshotRef, LeaseId, NetworkMode, ReadonlySnapshotHandle, RemountWorkspaceRequest,
    RemountWorkspaceResult, WorkspaceError, WorkspaceHandle, WorkspaceLaunchContext,
    WorkspaceService,
};

struct TestServices {
    command: Arc<operation_service::CommandOperationService>,
    services: OperationServices,
}

struct FakeWorkspaceService {
    create_results: Mutex<VecDeque<Result<WorkspaceHandle, WorkspaceError>>>,
}

impl FakeWorkspaceService {
    fn new() -> Self {
        Self {
            create_results: Mutex::new(VecDeque::new()),
        }
    }

    fn push_create_result(&self, result: Result<WorkspaceHandle, WorkspaceError>) {
        self.create_results
            .lock()
            .expect("test operation succeeds")
            .push_back(result);
    }
}

impl WorkspaceService for FakeWorkspaceService {
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

#[derive(Debug, Default)]
struct FakeLaunchDriver;

impl FakeLaunchDriver {
    fn new() -> Self {
        Self
    }
}

impl CommandLaunchDriver for FakeLaunchDriver {
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

fn build_services(fake: Arc<FakeWorkspaceService>) -> TestServices {
    let workspace = Arc::new(WorkspaceManagerService::new(fake));
    let command = Arc::new(
        operation_service::CommandOperationService::with_launch_driver_for_test(
            Arc::clone(&workspace),
            test_command_config(),
            Arc::new(FakeLaunchDriver::new()),
        ),
    );
    let remount = Arc::new(WorkspaceRemountService::new(
        Arc::clone(&workspace),
        Arc::clone(&command),
        WorkspaceRemountOptions::default(),
    ));
    let services = OperationServices::new(workspace, Arc::clone(&command), remount);

    TestServices { command, services }
}

fn workspace_handle(
    workspace_id: &str,
    caller_id: &str,
    lease_id: &str,
    workspace_root: PathBuf,
    network: NetworkMode,
) -> WorkspaceHandle {
    let snapshot = LayerStackSnapshotRef {
        lease_id: LeaseId(lease_id.to_owned()),
        manifest_version: 1,
        root_hash: "root".to_owned(),
        layer_paths: vec![PathBuf::from("/lower/one")],
    };
    WorkspaceHandle {
        id: workspace::WorkspaceId(workspace_id.to_owned()),
        owner: CallerId(caller_id.to_owned()),
        workspace_root,
        network,
        base_revision: BaseRevision {
            version: 1,
            root_hash: "root".to_owned(),
            layer_count: 1,
        },
        snapshot,
        launch: Some(test_launch_context()),
    }
}

fn test_command_config() -> command::CommandConfig {
    command::CommandConfig {
        scratch_root: std::env::temp_dir().join(format!(
            "operation-service-ownership-test-{}-{}",
            std::process::id(),
            unique_suffix()
        )),
        ..command::CommandConfig::default()
    }
}

fn test_launch_context() -> WorkspaceLaunchContext {
    let root = std::env::temp_dir().join(format!(
        "operation-service-ownership-launch-{}",
        unique_suffix()
    ));
    WorkspaceLaunchContext {
        upperdir: root.join("upper"),
        workdir: root.join("work"),
        namespace_fds: None,
        cgroup_path: None,
    }
}

fn unique_suffix() -> u64 {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    COUNTER.fetch_add(1, Ordering::Relaxed)
}

fn context(caller_id: &str) -> CommandCallContext {
    CommandCallContext {
        caller_id: CallerId(caller_id.to_owned()),
        trace: OperationTraceContext,
    }
}

fn exec_input(caller_id: &str, workspace_root: PathBuf) -> ExecCommandInput {
    ExecCommandInput {
        caller_id: CallerId(caller_id.to_owned()),
        workspace_root,
        workspace_id: None,
        cmd: "cat".to_owned(),
        cwd: None,
        timeout_seconds: None,
        yield_time_ms: Some(0),
    }
}

fn command_service_with_active_command() -> (TestServices, CommandId) {
    let fake = Arc::new(FakeWorkspaceService::new());
    let workspace_root = PathBuf::from("/workspace/one-shot");
    fake.push_create_result(Ok(workspace_handle(
        "workspace-one-shot",
        "caller-owner",
        "lease-1",
        workspace_root.clone(),
        NetworkMode::Host,
    )));
    let env = build_services(fake);
    let output = env
        .services
        .exec_command(
            exec_input("caller-owner", workspace_root),
            OperationTraceContext,
        )
        .expect("active command starts");
    let command_id = output.command_id.expect("running command id is returned");
    (env, command_id)
}

#[test]
fn command_ownership_rejects_wrong_caller_for_active_poll() {
    let (env, command_id) = command_service_with_active_command();

    let error = env
        .command
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
                && expected == CallerId("caller-owner".to_owned())
                && actual == CallerId("caller-other".to_owned())
    ));
}

#[test]
fn command_ownership_validates_stdin_against_active_owner() {
    let (env, command_id) = command_service_with_active_command();

    let error = env
        .command
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
                && expected == CallerId("caller-owner".to_owned())
                && actual == CallerId("caller-other".to_owned())
    ));

    let output = env
        .command
        .write_stdin(
            WriteStdinInput {
                command_id,
                chars: "hello\n".to_owned(),
                yield_time_ms: Some(0),
            },
            context("caller-owner"),
        )
        .expect("owner can write stdin");
    assert_eq!(output.status, CommandStatus::Running);
}

#[test]
fn command_ownership_rejects_wrong_caller_for_active_read() {
    let (env, command_id) = command_service_with_active_command();

    let error = env
        .command
        .read_lines(
            ReadCommandLinesInput {
                command_id: command_id.clone(),
                offset: 0,
                limit: 1,
            },
            context("caller-other"),
        )
        .expect_err("wrong caller cannot read active command output");

    assert!(matches!(
        error,
        CommandServiceError::CommandCallerMismatch { command_id: id, expected, actual }
            if id == command_id
                && expected == CallerId("caller-owner".to_owned())
                && actual == CallerId("caller-other".to_owned())
    ));
}

#[test]
fn command_ownership_cancel_rejects_wrong_caller_and_marks_owner_request() {
    let (env, command_id) = command_service_with_active_command();

    let error = env
        .command
        .cancel(
            CancelCommandInput {
                command_id: command_id.clone(),
            },
            context("caller-other"),
        )
        .expect_err("wrong caller cannot cancel active command");
    assert!(matches!(
        error,
        CommandServiceError::CommandCallerMismatch { command_id: id, expected, actual }
            if id == command_id
                && expected == CallerId("caller-owner".to_owned())
                && actual == CallerId("caller-other".to_owned())
    ));

    let output = env
        .command
        .cancel(
            CancelCommandInput {
                command_id: command_id.clone(),
            },
            context("caller-owner"),
        )
        .expect("owner can cancel active command");

    assert_eq!(output.status, CommandStatus::Running);
}
