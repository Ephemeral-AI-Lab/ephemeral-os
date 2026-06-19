use std::collections::{BTreeMap, VecDeque};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use crate::command::{
    CancelCommandInput, CommandCallContext, CommandFinalizationOutcome, CommandFinalizedPolicy,
    CommandId, CommandLaunchDriver, CommandServiceError, CommandStatus, ExecCommandInput,
    FinalizationState, OperationTraceContext, PollCommandInput, ReadCommandLinesInput,
};
use crate::workspace_manager::WorkspaceManagerService;
use crate::workspace_remount::{WorkspaceRemountOptions, WorkspaceRemountService};
use crate::OperationServices;
use command::process::{
    CommandProcess, CommandProcessExit, CommandProcessSpawn, CommandProcessSpec, KillReason,
};
use command::yield_wait_loop::WaitOutcome;
use layerstack::{LayerChange, LayerPath, LayerStack};
use workspace::{
    CallerId, CaptureChangesRequest, CapturedWorkspaceChanges, ChangedPathKind,
    CreateWorkspaceRequest, DestroyWorkspaceRequest, DestroyWorkspaceResult, LatestSnapshotRequest,
    LayerStackSnapshotRef, LeaseId, NetworkMode, ProtectedPathDrop, ReadonlySnapshotHandle,
    RemountWorkspaceRequest, RemountWorkspaceResult, WorkspaceError, WorkspaceHandle, WorkspaceId,
    WorkspaceLaunchContext, WorkspaceLaunchNamespaceFds, WorkspaceService,
};

type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

struct TestEnv {
    workspace: Arc<WorkspaceManagerService>,
    command: Arc<crate::CommandOperationService>,
    services: OperationServices,
}

struct FakeWorkspaceService {
    create_results: Mutex<VecDeque<Result<WorkspaceHandle, WorkspaceError>>>,
    capture_results: Mutex<VecDeque<Result<CapturedWorkspaceChanges, WorkspaceError>>>,
    destroy_results: Mutex<VecDeque<Result<DestroyWorkspaceResult, WorkspaceError>>>,
    capture_requests: Mutex<Vec<CaptureChangesRequest>>,
    capture_calls: Mutex<Vec<WorkspaceId>>,
    destroy_calls: Mutex<Vec<WorkspaceId>>,
}

impl FakeWorkspaceService {
    fn new() -> Self {
        Self {
            create_results: Mutex::new(VecDeque::new()),
            capture_results: Mutex::new(VecDeque::new()),
            destroy_results: Mutex::new(VecDeque::new()),
            capture_requests: Mutex::new(Vec::new()),
            capture_calls: Mutex::new(Vec::new()),
            destroy_calls: Mutex::new(Vec::new()),
        }
    }

    fn push_create_result(&self, result: Result<WorkspaceHandle, WorkspaceError>) {
        self.create_results
            .lock()
            .expect("test operation succeeds")
            .push_back(result);
    }

    fn push_capture_result(&self, result: Result<CapturedWorkspaceChanges, WorkspaceError>) {
        self.capture_results
            .lock()
            .expect("test operation succeeds")
            .push_back(result);
    }

    fn push_destroy_result(&self, result: Result<DestroyWorkspaceResult, WorkspaceError>) {
        self.destroy_results
            .lock()
            .expect("test operation succeeds")
            .push_back(result);
    }

    fn capture_requests(&self) -> Vec<CaptureChangesRequest> {
        self.capture_requests
            .lock()
            .expect("test operation succeeds")
            .clone()
    }

    fn capture_calls(&self) -> Vec<WorkspaceId> {
        self.capture_calls
            .lock()
            .expect("test operation succeeds")
            .clone()
    }

    fn destroy_calls(&self) -> Vec<WorkspaceId> {
        self.destroy_calls
            .lock()
            .expect("test operation succeeds")
            .clone()
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
        handle: &WorkspaceHandle,
        request: CaptureChangesRequest,
    ) -> Result<CapturedWorkspaceChanges, WorkspaceError> {
        self.capture_calls
            .lock()
            .expect("test operation succeeds")
            .push(handle.id.clone());
        self.capture_requests
            .lock()
            .expect("test operation succeeds")
            .push(request);
        self.capture_results
            .lock()
            .expect("test operation succeeds")
            .pop_front()
            .unwrap_or_else(|| {
                Err(WorkspaceError::Capture {
                    message: "capture result not configured".to_owned(),
                })
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
        self.destroy_calls
            .lock()
            .expect("test operation succeeds")
            .push(handle.id.clone());
        self.destroy_results
            .lock()
            .expect("test operation succeeds")
            .pop_front()
            .unwrap_or_else(|| Ok(destroy_result(&handle)))
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

struct LayerFixture {
    base: PathBuf,
    root: PathBuf,
    snapshot: layerstack::service::Snapshot,
}

impl LayerFixture {
    fn new(label: &str) -> TestResult<Self> {
        let base = std::env::temp_dir().join(format!(
            "operation-service-command-finalize-{label}-{}",
            unique_suffix()
        ));
        let _ = std::fs::remove_dir_all(&base);
        let root = base.join("layer-stack");
        let layer = root.join("layers").join("B000001-base");
        std::fs::create_dir_all(&layer)?;
        std::fs::create_dir_all(root.join("staging"))?;
        std::fs::write(layer.join("README.md"), "# README\n")?;
        std::fs::write(
            root.join("manifest.json"),
            r#"{
  "schema_version": 1,
  "version": 1,
  "layers": [{"layer_id": "B000001-base", "path": "layers/B000001-base"}]
}
"#,
        )?;
        let snapshot = layerstack::service::acquire_snapshot(&root, label)?;
        Ok(Self {
            base,
            root,
            snapshot,
        })
    }
}

impl Drop for LayerFixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.base);
    }
}

fn unique_suffix() -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    format!(
        "{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    )
}

fn build_env(fake: Arc<FakeWorkspaceService>) -> TestEnv {
    let workspace = Arc::new(WorkspaceManagerService::new(fake));
    let command = Arc::new(crate::CommandOperationService::with_launch_driver_for_test(
        Arc::clone(&workspace),
        test_command_config(),
        Arc::new(FakeLaunchDriver),
    ));
    let remount = Arc::new(WorkspaceRemountService::new(
        Arc::clone(&workspace),
        Arc::clone(&command),
        WorkspaceRemountOptions::default(),
    ));
    let services = OperationServices::new(Arc::clone(&workspace), Arc::clone(&command), remount);

    TestEnv {
        workspace,
        command,
        services,
    }
}

fn test_command_config() -> command::CommandConfig {
    command::CommandConfig {
        scratch_root: std::env::temp_dir().join(format!(
            "operation-service-command-finalize-scratch-{}",
            unique_suffix()
        )),
        ..command::CommandConfig::default()
    }
}

fn workspace_handle(
    workspace_id: &str,
    caller_id: &str,
    fixture: &LayerFixture,
) -> WorkspaceHandle {
    let snapshot = LayerStackSnapshotRef {
        lease_id: LeaseId(fixture.snapshot.lease_id.clone()),
        manifest_version: fixture.snapshot.manifest_version,
        root_hash: fixture.snapshot.root_hash.clone(),
        layer_paths: fixture.snapshot.layer_paths.clone(),
    };
    WorkspaceHandle {
        id: WorkspaceId(workspace_id.to_owned()),
        owner: CallerId(caller_id.to_owned()),
        workspace_root: fixture.root.clone(),
        network: NetworkMode::Host,
        base_revision: snapshot.base_revision(),
        snapshot,
        launch: Some(test_launch_context(fixture)),
    }
}

fn test_launch_context(fixture: &LayerFixture) -> WorkspaceLaunchContext {
    WorkspaceLaunchContext {
        upperdir: fixture.base.join("upper"),
        workdir: fixture.base.join("work"),
        namespace_fds: Some(WorkspaceLaunchNamespaceFds {
            user: Some(10),
            mnt: Some(11),
            pid: Some(12),
            net: None,
        }),
        cgroup_path: None,
    }
}

fn destroy_result(handle: &WorkspaceHandle) -> DestroyWorkspaceResult {
    DestroyWorkspaceResult {
        workspace_id: handle.id.clone(),
        owner: handle.owner.clone(),
        evicted_upperdir_bytes: 7,
        lifetime_s: 0.5,
        lease_released: Some(true),
        lease_release_error: None,
        active_leases_after: 0,
    }
}

fn captured_changes(
    handle: &WorkspaceHandle,
    changes: Vec<LayerChange>,
    spool_dir: Option<PathBuf>,
) -> CapturedWorkspaceChanges {
    let changed_paths = changes
        .iter()
        .map(|change| change.path().as_str().to_owned())
        .collect::<Vec<_>>();
    let changed_path_kinds = changes
        .iter()
        .map(|change| {
            (
                change.path().as_str().to_owned(),
                ChangedPathKind::from(change),
            )
        })
        .collect::<BTreeMap<_, _>>();
    CapturedWorkspaceChanges {
        workspace_id: handle.id.clone(),
        base_revision: handle.base_revision.clone(),
        changed_paths,
        changed_path_kinds,
        protected_drops: Vec::<ProtectedPathDrop>::new(),
        stats: None,
        route_stats: layerstack::CaptureRouteStats {
            gated_path_count: changes.len(),
            ..layerstack::CaptureRouteStats::default()
        },
        metadata_path_count: changes.len(),
        changes,
        spool_dir,
    }
}

fn exec_input(
    caller_id: &str,
    root: PathBuf,
    workspace_id: Option<WorkspaceId>,
) -> ExecCommandInput {
    ExecCommandInput {
        caller_id: CallerId(caller_id.to_owned()),
        workspace_root: root,
        workspace_id,
        cmd: "printf ok".to_owned(),
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

fn success_exit(stdout: &str) -> CommandProcessExit {
    CommandProcessExit {
        status: "completed".to_owned(),
        exit_code: 0,
        signal: None,
        runner_result: None,
        stdout: stdout.to_owned(),
        elapsed_s: 0.1,
        kill: None,
    }
}

fn failed_exit() -> CommandProcessExit {
    CommandProcessExit {
        status: "failed".to_owned(),
        exit_code: 2,
        signal: None,
        runner_result: None,
        stdout: "failed\n".to_owned(),
        elapsed_s: 0.1,
        kill: None,
    }
}

fn killed_exit(kill: KillReason) -> CommandProcessExit {
    let (status, exit_code) = match kill {
        KillReason::Cancelled => ("cancelled", 130),
        KillReason::TimedOut => ("timed_out", 124),
    };
    CommandProcessExit {
        status: status.to_owned(),
        exit_code,
        signal: None,
        runner_result: None,
        stdout: format!("{status}\n"),
        elapsed_s: 0.1,
        kill: Some(kill),
    }
}

fn start_one_shot(
    env: &TestEnv,
    fixture: &LayerFixture,
    caller_id: &str,
) -> Result<CommandId, CommandServiceError> {
    let output = env.services.exec_command(
        exec_input(caller_id, fixture.root.clone(), None),
        OperationTraceContext,
    )?;
    Ok(output.command_id.expect("running command id is returned"))
}

#[test]
fn command_finalize_successful_one_shot_captures_publishes_then_destroys() -> TestResult {
    let fixture = LayerFixture::new("success")?;
    let handle = workspace_handle("workspace-one-shot", "caller-1", &fixture);
    let spool_dir = fixture.base.join("capture-spool");
    std::fs::create_dir_all(&spool_dir)?;
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(handle.clone()));
    fake.push_capture_result(Ok(captured_changes(
        &handle,
        vec![LayerChange::Write {
            path: LayerPath::parse("published.txt")?,
            content: b"published\n".to_vec(),
        }],
        Some(spool_dir.clone()),
    )));
    let env = build_env(Arc::clone(&fake));
    let command_id = start_one_shot(&env, &fixture, "caller-1")?;

    let result = env
        .command
        .finalize_command(command_id.clone(), success_exit("done\n"))?;

    assert_eq!(result.status, CommandStatus::Completed);
    assert_eq!(result.stdout, "done\n");
    assert_eq!(
        fake.capture_calls(),
        vec![WorkspaceId("workspace-one-shot".to_owned())]
    );
    assert_eq!(fake.capture_requests().len(), 1);
    assert!(fake.capture_requests()[0].include_stats);
    assert_eq!(
        fake.destroy_calls(),
        vec![WorkspaceId("workspace-one-shot".to_owned())]
    );
    assert_eq!(
        LayerStack::open(fixture.root.clone())?
            .read_text("published.txt")?
            .0,
        "published\n"
    );

    let poll = env.command.poll(
        PollCommandInput {
            command_id,
            last_n_lines: None,
        },
        context("caller-1"),
    )?;
    let finalized = poll.finalized.expect("completed record keeps metadata");
    assert_eq!(
        finalized.policy,
        CommandFinalizedPolicy::OneShotPublishThenDestroy
    );
    assert_eq!(finalized.outcome, CommandFinalizationOutcome::Published);
    assert_eq!(finalized.changed_paths, vec!["published.txt"]);
    assert_eq!(finalized.captured_change_count, 1);
    assert_eq!(finalized.metadata_path_count, 1);
    assert!(finalized.published_manifest_version.is_some());
    assert!(finalized.destroy.is_some());
    assert!(finalized.spool_dir_cleaned);
    assert!(!spool_dir.exists());
    Ok(())
}

#[test]
fn command_finalize_non_success_cancel_and_timeout_one_shots_discard_without_capture() -> TestResult
{
    for (label, process_exit) in [
        ("failed", failed_exit()),
        ("cancelled", killed_exit(KillReason::Cancelled)),
        ("timed-out", killed_exit(KillReason::TimedOut)),
    ] {
        let fixture = LayerFixture::new(label)?;
        let handle = workspace_handle("workspace-one-shot", "caller-1", &fixture);
        let fake = Arc::new(FakeWorkspaceService::new());
        fake.push_create_result(Ok(handle));
        let env = build_env(Arc::clone(&fake));
        let command_id = start_one_shot(&env, &fixture, "caller-1")?;

        let result = env
            .command
            .finalize_command(command_id.clone(), process_exit)?;

        assert_eq!(result.status, CommandStatus::Failed);
        assert!(fake.capture_calls().is_empty(), "{label} captured changes");
        assert_eq!(
            fake.destroy_calls(),
            vec![WorkspaceId("workspace-one-shot".to_owned())],
            "{label} did not destroy one-shot workspace"
        );
        let finalized = env
            .command
            .poll(
                PollCommandInput {
                    command_id,
                    last_n_lines: None,
                },
                context("caller-1"),
            )?
            .finalized
            .expect("completed discard metadata retained");
        assert_eq!(finalized.outcome, CommandFinalizationOutcome::Discarded);
        assert_eq!(
            finalized.policy,
            CommandFinalizedPolicy::OneShotPublishThenDestroy
        );
    }
    Ok(())
}

#[test]
fn command_finalize_destroy_failure_is_reportable_and_keeps_cleanup_state() -> TestResult {
    let fixture = LayerFixture::new("destroy-failure")?;
    let handle = workspace_handle("workspace-one-shot", "caller-1", &fixture);
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(handle));
    fake.push_destroy_result(Err(WorkspaceError::Setup {
        step: "destroy failed".to_owned(),
    }));
    let env = build_env(Arc::clone(&fake));
    let command_id = start_one_shot(&env, &fixture, "caller-1")?;

    let error = env
        .command
        .finalize_command(command_id.clone(), failed_exit())
        .expect_err("destroy failure is retained as finalization failure");

    assert!(matches!(
        error,
        CommandServiceError::CommandFinalizationFailed {
            command_id: id,
            error,
            finalized: Some(finalized),
        }
            if id == command_id && error.contains("destroy failed")
                && finalized.outcome == CommandFinalizationOutcome::Discarded
    ));
    assert!(fake.capture_calls().is_empty());
    assert_eq!(
        fake.destroy_calls(),
        vec![WorkspaceId("workspace-one-shot".to_owned())]
    );
    let poll_error = env
        .command
        .poll(
            PollCommandInput {
                command_id: command_id.clone(),
                last_n_lines: None,
            },
            context("caller-1"),
        )
        .expect_err("failed finalization remains reportable");
    assert!(matches!(
        poll_error,
        CommandServiceError::CommandFinalizationFailed {
            command_id: id,
            finalized: Some(finalized),
            ..
        } if id == command_id && finalized.outcome == CommandFinalizationOutcome::Discarded
    ));
    Ok(())
}

#[test]
fn command_finalize_capture_failure_records_failed_finalization_without_destroy() -> TestResult {
    let fixture = LayerFixture::new("capture-failure")?;
    let handle = workspace_handle("workspace-one-shot", "caller-1", &fixture);
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(handle));
    fake.push_capture_result(Err(WorkspaceError::Capture {
        message: "capture failed".to_owned(),
    }));
    let env = build_env(Arc::clone(&fake));
    let command_id = start_one_shot(&env, &fixture, "caller-1")?;

    let error = env
        .command
        .finalize_command(command_id.clone(), success_exit("done\n"))
        .expect_err("capture failure records failed finalization");

    assert!(matches!(
        error,
        CommandServiceError::CommandFinalizationFailed {
            command_id: id,
            error,
            finalized: None,
        }
            if id == command_id && error.contains("capture failed")
    ));
    assert_eq!(
        fake.capture_calls(),
        vec![WorkspaceId("workspace-one-shot".to_owned())]
    );
    assert!(fake.destroy_calls().is_empty());
    let poll_error = env
        .command
        .poll(
            PollCommandInput {
                command_id: command_id.clone(),
                last_n_lines: None,
            },
            context("caller-1"),
        )
        .expect_err("failed finalization remains reportable");
    assert!(matches!(
        poll_error,
        CommandServiceError::CommandFinalizationFailed {
            command_id: id,
            finalized: None,
            ..
        } if id == command_id
    ));
    Ok(())
}

#[test]
fn command_finalize_publish_failure_records_failed_finalization_without_destroy() -> TestResult {
    let fixture = LayerFixture::new("publish-failure")?;
    let mut handle = workspace_handle("workspace-one-shot", "caller-1", &fixture);
    handle.snapshot.manifest_version = -1;
    handle.base_revision = handle.snapshot.base_revision();
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(handle.clone()));
    fake.push_capture_result(Ok(captured_changes(
        &handle,
        vec![LayerChange::Write {
            path: LayerPath::parse("publish-failed.txt")?,
            content: b"not published\n".to_vec(),
        }],
        None,
    )));
    let env = build_env(Arc::clone(&fake));
    let command_id = start_one_shot(&env, &fixture, "caller-1")?;

    let error = env
        .command
        .finalize_command(command_id.clone(), success_exit("done\n"))
        .expect_err("publish failure records failed finalization");

    assert!(matches!(
        error,
        CommandServiceError::CommandFinalizationFailed {
            command_id: id,
            error,
            finalized: None,
        } if id == command_id && error.contains("manifest version must be non-negative")
    ));
    assert_eq!(
        fake.capture_calls(),
        vec![WorkspaceId("workspace-one-shot".to_owned())]
    );
    assert!(fake.destroy_calls().is_empty());
    let poll_error = env
        .command
        .poll(
            PollCommandInput {
                command_id: command_id.clone(),
                last_n_lines: None,
            },
            context("caller-1"),
        )
        .expect_err("failed publish finalization remains reportable");
    assert!(matches!(
        poll_error,
        CommandServiceError::CommandFinalizationFailed {
            command_id: id,
            finalized: None,
            ..
        } if id == command_id
    ));
    Ok(())
}

#[test]
fn command_finalize_success_destroy_failure_retains_published_metadata() -> TestResult {
    let fixture = LayerFixture::new("publish-then-destroy-failure")?;
    let handle = workspace_handle("workspace-one-shot", "caller-1", &fixture);
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(handle.clone()));
    fake.push_capture_result(Ok(captured_changes(
        &handle,
        vec![LayerChange::Write {
            path: LayerPath::parse("published-before-destroy.txt")?,
            content: b"published before destroy\n".to_vec(),
        }],
        None,
    )));
    fake.push_destroy_result(Err(WorkspaceError::Setup {
        step: "destroy failed".to_owned(),
    }));
    let env = build_env(Arc::clone(&fake));
    let command_id = start_one_shot(&env, &fixture, "caller-1")?;

    let error = env
        .command
        .finalize_command(command_id.clone(), success_exit("done\n"))
        .expect_err("destroy failure retains published metadata");

    assert!(matches!(
        error,
        CommandServiceError::CommandFinalizationFailed {
            command_id: id,
            error,
            finalized: Some(finalized),
        } if id == command_id
            && error.contains("destroy failed")
            && finalized.outcome == CommandFinalizationOutcome::Published
            && finalized.changed_paths == vec!["published-before-destroy.txt".to_owned()]
            && finalized.destroy.is_none()
    ));
    assert_eq!(
        LayerStack::open(fixture.root.clone())?
            .read_text("published-before-destroy.txt")?
            .0,
        "published before destroy\n"
    );
    let active = env
        .command
        .process_store()
        .active(&command_id)
        .expect("failed finalization keeps active cleanup state");
    assert!(matches!(
        &active.finalization,
        FinalizationState::Failed {
            finalized: Some(finalized),
            ..
        } if finalized.outcome == CommandFinalizationOutcome::Published
            && finalized.changed_paths == vec!["published-before-destroy.txt".to_owned()]
            && finalized.destroy.is_none()
    ));
    Ok(())
}

#[test]
fn command_finalize_failed_active_authorizes_before_reporting_failure_details() -> TestResult {
    let fixture = LayerFixture::new("failed-active-auth")?;
    let handle = workspace_handle("workspace-one-shot", "caller-1", &fixture);
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(handle));
    fake.push_capture_result(Err(WorkspaceError::Capture {
        message: "sensitive capture path".to_owned(),
    }));
    let env = build_env(Arc::clone(&fake));
    let command_id = start_one_shot(&env, &fixture, "caller-1")?;

    env.command
        .finalize_command(command_id.clone(), success_exit("done\n"))
        .expect_err("capture failure records failed finalization");

    let poll_error = env
        .command
        .poll(
            PollCommandInput {
                command_id: command_id.clone(),
                last_n_lines: None,
            },
            context("caller-other"),
        )
        .expect_err("wrong caller cannot poll failed active command");
    let read_error = env
        .command
        .read_lines(
            ReadCommandLinesInput {
                command_id: command_id.clone(),
                offset: 0,
                limit: 1,
            },
            context("caller-other"),
        )
        .expect_err("wrong caller cannot read failed active command");
    let cancel_error = env
        .command
        .cancel(
            CancelCommandInput {
                command_id: command_id.clone(),
            },
            context("caller-other"),
        )
        .expect_err("wrong caller cannot cancel failed active command");

    for error in [poll_error, read_error, cancel_error] {
        assert!(
            !error.to_string().contains("sensitive capture path"),
            "authorization error leaked finalization details: {error}"
        );
        assert!(matches!(
            error,
            CommandServiceError::CommandCallerMismatch { command_id: id, expected, actual }
                if id == command_id
                    && expected == CallerId("caller-1".to_owned())
                    && actual == CallerId("caller-other".to_owned())
        ));
    }
    Ok(())
}

#[test]
fn command_finalize_session_does_not_capture_publish_destroy_or_refresh_snapshot() -> TestResult {
    let fixture = LayerFixture::new("session")?;
    let handle = workspace_handle("workspace-session", "caller-1", &fixture);
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(handle));
    let env = build_env(Arc::clone(&fake));
    let handler = env.workspace.create_private_workspace(
        CallerId("caller-1".to_owned()),
        fixture.root.clone(),
        NetworkMode::Host,
    )?;
    let command = env.services.exec_command(
        exec_input(
            "caller-1",
            fixture.root.clone(),
            Some(handler.workspace_id.clone()),
        ),
        OperationTraceContext,
    )?;
    let command_id = command.command_id.expect("running command id is returned");

    env.command
        .finalize_command(command_id.clone(), success_exit("session done\n"))?;

    assert!(fake.capture_calls().is_empty());
    assert!(fake.destroy_calls().is_empty());
    let resolved = env.workspace.resolve(
        WorkspaceId("workspace-session".to_owned()),
        CallerId("caller-1".to_owned()),
    )?;
    assert_eq!(resolved.snapshot.manifest_version, 1);
    assert_eq!(resolved.layer_paths, fixture.snapshot.layer_paths);
    let finalized = env
        .command
        .poll(
            PollCommandInput {
                command_id,
                last_n_lines: None,
            },
            context("caller-1"),
        )?
        .finalized
        .expect("session finalization metadata retained");
    assert_eq!(finalized.policy, CommandFinalizedPolicy::Session);
    assert_eq!(
        finalized.outcome,
        CommandFinalizationOutcome::SessionComplete
    );
    Ok(())
}

#[test]
fn command_completion_retains_authorization_after_command_finalize() -> TestResult {
    let fixture = LayerFixture::new("completion-retention")?;
    let handle = workspace_handle("workspace-one-shot", "caller-1", &fixture);
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(handle.clone()));
    fake.push_capture_result(Ok(captured_changes(&handle, Vec::new(), None)));
    let env = build_env(fake);
    let command_id = start_one_shot(&env, &fixture, "caller-1")?;
    let transcript_path = env
        .command
        .config()
        .scratch_root
        .join(&command_id.0)
        .join("transcript.log");
    std::fs::write(&transcript_path, "line one\nline two\n")?;

    env.command
        .finalize_command(command_id.clone(), success_exit("line one\nline two\n"))?;

    let lines = env.command.read_lines(
        ReadCommandLinesInput {
            command_id: command_id.clone(),
            offset: 1,
            limit: 1,
        },
        context("caller-1"),
    )?;
    assert_eq!(lines.total_lines, 2);
    assert_eq!(lines.output[0].text, "line two");

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
        .expect_err("retained completed record still validates caller");
    assert!(matches!(
        error,
        CommandServiceError::CommandCallerMismatch { command_id: id, expected, actual }
            if id == command_id
                && expected == CallerId("caller-1".to_owned())
                && actual == CallerId("caller-other".to_owned())
    ));
    Ok(())
}
