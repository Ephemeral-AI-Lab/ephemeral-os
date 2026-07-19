use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use sandbox_runtime::command::ExecCommandInput;
use sandbox_runtime::workspace_session::FinalizePolicy;
use sandbox_runtime::SandboxRuntimeOperations;
use sandbox_runtime_workspace::{CapturedWorkspaceChanges, NetworkProfile, WorkspaceError};

mod support;

use support::{FakeLaunchDriver, FakeRunnerScript, FakeWorkspaceService};

fn operations(
    fake: Arc<FakeWorkspaceService>,
    launch_driver: Arc<FakeLaunchDriver>,
) -> SandboxRuntimeOperations {
    let layerstack =
        support::observed_layerstack_service(sandbox_observability_telemetry::Observer::disabled());
    let services = support::build_services_with_launch_driver_and_layerstack(
        fake,
        launch_driver,
        Arc::clone(&layerstack),
    );
    SandboxRuntimeOperations::new(
        services.command,
        services.workspace,
        layerstack,
        support::test_file_service(),
    )
}

fn create(
    operations: &SandboxRuntimeOperations,
    fake: &FakeWorkspaceService,
    id: &str,
    policy: FinalizePolicy,
) -> sandbox_runtime::workspace_session::WorkspaceSessionHandler {
    fake.push_create_result(Ok(support::workspace_handle(
        id,
        &format!("lease-{id}"),
        PathBuf::from("/workspace"),
        NetworkProfile::Shared,
    )));
    operations
        .workspace_session
        .create_workspace_session(support::create_request_with_policy(policy))
        .expect("session creates")
}

fn empty_capture(
    handler: &sandbox_runtime::workspace_session::WorkspaceSessionHandler,
) -> CapturedWorkspaceChanges {
    CapturedWorkspaceChanges {
        workspace_session_id: handler.workspace_session_id.clone(),
        base_revision: handler.handle.base_revision().clone(),
        base_manifest: support::test_manifest(),
        changed_path_kinds: Default::default(),
        protected_drops: Vec::new(),
        stats: None,
        metadata_path_count: 0,
        changed_paths: Vec::new(),
        changes: Vec::new(),
    }
}

#[test]
fn concurrent_shutdown_callers_join_one_exact_teardown_and_reuse_the_report() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let operations = operations(Arc::clone(&fake), Arc::new(FakeLaunchDriver::new()));
    create(&operations, &fake, "shutdown-once", FinalizePolicy::NoOp);
    let (destroy_entered, destroy_release) = fake.park_next_destroy();

    let first_operations = operations.clone();
    let first = std::thread::spawn(move || first_operations.shutdown());
    destroy_entered
        .recv_timeout(Duration::from_secs(1))
        .expect("first shutdown owns raw destroy");

    let second_operations = operations.clone();
    let (second_done_tx, second_done_rx) = std::sync::mpsc::channel();
    let second = std::thread::spawn(move || {
        let report = second_operations.shutdown();
        second_done_tx
            .send(())
            .expect("report completion is observable");
        report
    });
    assert!(second_done_rx
        .recv_timeout(Duration::from_millis(50))
        .is_err());

    destroy_release.send(()).expect("release raw destroy");
    let first_report = first.join().expect("first shutdown joins");
    let second_report = second.join().expect("second shutdown joins");
    assert_eq!(first_report, second_report);
    assert_eq!(first_report.sessions_observed, 1);
    assert_eq!(first_report.sessions_converged, 1);
    assert!(first_report.is_complete());
    assert_eq!(fake.destroy_calls().len(), 1);

    assert_eq!(operations.shutdown(), first_report);
    assert_eq!(fake.destroy_calls().len(), 1);
}

#[test]
fn shutdown_continues_peer_teardown_and_retains_the_last_retry_failure() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let operations = operations(Arc::clone(&fake), Arc::new(FakeLaunchDriver::new()));
    create(&operations, &fake, "shutdown-a", FinalizePolicy::NoOp);
    create(&operations, &fake, "shutdown-b", FinalizePolicy::NoOp);
    fake.push_destroy_result(Err(WorkspaceError::Setup {
        step: "first teardown failure".to_owned(),
    }));
    fake.push_destroy_result(Ok(support::destroy_result(
        &support::workspace_handle_without_launch(
            "shutdown-b",
            "lease-shutdown-b-result",
            PathBuf::from("/workspace"),
            NetworkProfile::Shared,
        ),
    )));
    fake.push_destroy_result(Err(WorkspaceError::Setup {
        step: "second teardown failure".to_owned(),
    }));

    let report = operations.shutdown();

    assert_eq!(report.sessions_observed, 2);
    assert_eq!(report.sessions_converged, 1);
    assert!(!report.is_complete());
    assert_eq!(report.failures.len(), 1);
    assert_eq!(
        report.failures[0]
            .workspace_session_id
            .as_ref()
            .map(|id| id.0.as_str()),
        Some("shutdown-a")
    );
    assert!(report.failures[0]
        .diagnostic
        .contains("second teardown failure"));
    assert_eq!(
        fake.destroy_calls()
            .into_iter()
            .map(|id| id.0)
            .collect::<Vec<_>>(),
        ["shutdown-a", "shutdown-b", "shutdown-a"]
    );
    assert_eq!(operations.shutdown(), report);
    assert_eq!(fake.destroy_calls().len(), 3);
}

#[test]
fn shutdown_finalizes_an_idle_publish_policy_before_destroy() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let operations = operations(Arc::clone(&fake), Arc::new(FakeLaunchDriver::new()));
    let handler = create(
        &operations,
        &fake,
        "shutdown-publish",
        FinalizePolicy::PublishThenDestroy,
    );
    fake.push_capture_result(Ok(empty_capture(&handler)));

    let report = operations.shutdown();

    assert!(report.is_complete());
    assert_eq!(report.sessions_converged, 1);
    assert_eq!(
        fake.capture_calls().as_slice(),
        std::slice::from_ref(&handler.workspace_session_id)
    );
    assert_eq!(fake.destroy_calls(), [handler.workspace_session_id]);
}

#[test]
fn shutdown_cancels_and_joins_a_command_before_releasing_its_workspace() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let launch_driver = Arc::new(FakeLaunchDriver::new());
    launch_driver
        .launcher()
        .push_script(FakeRunnerScript::pending());
    let operations = operations(Arc::clone(&fake), Arc::clone(&launch_driver));
    let handler = create(&operations, &fake, "shutdown-command", FinalizePolicy::NoOp);
    let command = operations
        .command
        .exec_command(ExecCommandInput {
            workspace_session_id: Some(handler.workspace_session_id.clone()),
            cmd: "sleep 600".to_owned(),
            timeout_ms: None,
            yield_time_ms: Some(0),
        })
        .expect("command is admitted")
        .command_session_id
        .expect("pending command has an id");

    let report = operations.shutdown();

    assert!(report.is_complete());
    assert_eq!(report.sessions_converged, 1);
    assert_eq!(
        launch_driver.launcher().recorded_cancel_request_ids(),
        [command.0]
    );
    assert_eq!(fake.destroy_calls(), [handler.workspace_session_id]);
}

#[test]
fn shutdown_joins_an_explicit_destroy_flight_without_a_second_raw_call() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let operations = operations(Arc::clone(&fake), Arc::new(FakeLaunchDriver::new()));
    let handler = create(
        &operations,
        &fake,
        "shutdown-destroy-race",
        FinalizePolicy::NoOp,
    );
    let (destroy_entered, destroy_release) = fake.park_next_destroy();
    let explicit_workspace = Arc::clone(&operations.workspace_session);
    let explicit_id = handler.workspace_session_id.clone();
    let explicit =
        std::thread::spawn(move || explicit_workspace.guarded_destroy(explicit_id, None));
    destroy_entered
        .recv_timeout(Duration::from_secs(1))
        .expect("explicit destroy owns raw teardown");

    let shutdown_operations = operations.clone();
    let shutdown = std::thread::spawn(move || shutdown_operations.shutdown());
    std::thread::sleep(Duration::from_millis(25));
    destroy_release.send(()).expect("release explicit destroy");

    explicit
        .join()
        .expect("explicit destroy joins")
        .expect("explicit destroy succeeds");
    let report = shutdown.join().expect("shutdown joins");
    assert!(report.is_complete());
    assert_eq!(report.sessions_converged, 1);
    assert_eq!(fake.destroy_calls(), [handler.workspace_session_id]);
}
