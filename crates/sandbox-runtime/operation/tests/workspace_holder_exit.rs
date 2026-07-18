use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use sandbox_runtime::command::ExecCommandInput;
use sandbox_runtime::workspace_session::{
    FinalizationState, FinalizePolicy, HolderExitDispatcher, HolderExitDisposition,
    WorkspaceSessionError, WorkspaceSessionService,
};
use sandbox_runtime_workspace::{NetworkProfile, WorkspaceSessionId};

mod support;
use support::FakeWorkspaceService;

fn manager_with(fake: &Arc<FakeWorkspaceService>) -> WorkspaceSessionService {
    WorkspaceSessionService::new(
        support::fake_workspace_runtime(Arc::clone(fake)),
        support::observed_layerstack_service(sandbox_observability_telemetry::Observer::disabled()),
        sandbox_observability_telemetry::Observer::disabled(),
    )
}

fn create(
    manager: &WorkspaceSessionService,
    fake: &Arc<FakeWorkspaceService>,
    id: &str,
    policy: FinalizePolicy,
) -> sandbox_runtime::workspace_session::WorkspaceSessionHandler {
    fake.push_create_result(Ok(support::workspace_handle(
        id,
        &format!("lease-{id}"),
        PathBuf::from("/workspace"),
        NetworkProfile::Shared,
    )));
    manager
        .create_workspace_session(support::create_request_with_policy(policy))
        .expect("session creates")
}

fn wait_until(timeout: Duration, predicate: impl Fn() -> bool) {
    let deadline = Instant::now() + timeout;
    while !predicate() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(2));
    }
    assert!(predicate(), "condition did not converge within {timeout:?}");
}

#[test]
fn holder_exit_dispatches_cleanup_without_a_followup_request_or_snapshot() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let manager = Arc::new(manager_with(&fake));
    let dispatcher = HolderExitDispatcher::start(&manager)
        .expect("dispatcher starts")
        .expect("fake runtime exposes supervision");
    let failed = create(&manager, &fake, "autonomous", FinalizePolicy::NoOp);

    failed.handle.mark_holder_exited_for_test("signal:9");
    fake.notify_holder_exit();

    wait_until(Duration::from_secs(1), || {
        fake.destroy_calls() == vec![failed.workspace_session_id.clone()]
    });
    assert_eq!(
        manager.finalization_state_for_test(&failed.workspace_session_id),
        None,
        "the autonomous pass removed the session without any public read"
    );
    dispatcher.shutdown_and_join();
    assert!(dispatcher.is_joined_for_test());
}

#[test]
fn dispatched_exit_and_explicit_destroy_join_one_runtime_teardown() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let manager = Arc::new(manager_with(&fake));
    let dispatcher = HolderExitDispatcher::start(&manager)
        .expect("dispatcher starts")
        .expect("fake runtime exposes supervision");
    let failed = create(&manager, &fake, "dispatch-race", FinalizePolicy::NoOp);
    let (entered, release) = fake.park_next_destroy();

    failed.handle.mark_holder_exited_for_test("signal:9");
    fake.notify_holder_exit();
    entered
        .recv_timeout(Duration::from_secs(1))
        .expect("dispatcher enters raw teardown");

    let explicit_manager = Arc::clone(&manager);
    let explicit_handler = failed.clone();
    let explicit = std::thread::spawn(move || {
        explicit_manager.destroy_session(explicit_handler, Default::default())
    });
    std::thread::sleep(Duration::from_millis(20));
    release.send(()).expect("release dispatcher teardown");

    explicit
        .join()
        .expect("explicit destroy joins")
        .expect("joined destroy succeeds");
    wait_until(Duration::from_secs(1), || {
        manager
            .finalization_state_for_test(&failed.workspace_session_id)
            .is_none()
    });
    assert_eq!(fake.destroy_calls(), vec![failed.workspace_session_id]);
    dispatcher.shutdown_and_join();
    assert!(dispatcher.is_joined_for_test());
}

#[test]
fn dispatched_cleanup_failure_stays_visible_and_retryable() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let manager = Arc::new(manager_with(&fake));
    let dispatcher = HolderExitDispatcher::start(&manager)
        .expect("dispatcher starts")
        .expect("fake runtime exposes supervision");
    let failed = create(&manager, &fake, "dispatch-retry", FinalizePolicy::NoOp);
    for attempt in 0..HolderExitDispatcher::cleanup_attempt_limit_for_test() {
        fake.push_destroy_result(Err(sandbox_runtime_workspace::WorkspaceError::Cleanup {
            workspace_session_id: failed.workspace_session_id.0.clone(),
            failures: vec![format!("Mounts: injected failure {attempt}")],
        }));
    }

    failed.handle.mark_holder_exited_for_test("signal:9");
    fake.notify_holder_exit();
    wait_until(Duration::from_secs(1), || {
        fake.destroy_calls().len() == HolderExitDispatcher::cleanup_attempt_limit_for_test()
    });
    std::thread::sleep(Duration::from_millis(100));
    assert_eq!(
        fake.destroy_calls().len(),
        HolderExitDispatcher::cleanup_attempt_limit_for_test(),
        "bounded retries stop without an idle cleanup loop"
    );
    assert_eq!(
        manager.finalization_state_for_test(&failed.workspace_session_id),
        Some(FinalizationState::FinalizeFailed)
    );

    let retried = manager.reconcile_holder_exits();
    assert!(matches!(
        retried.as_slice(),
        [outcome] if outcome.disposition == HolderExitDisposition::Destroyed
    ));
    assert_eq!(
        fake.destroy_calls().len(),
        HolderExitDispatcher::cleanup_attempt_limit_for_test() + 1
    );
    dispatcher.shutdown_and_join();
    assert!(dispatcher.is_joined_for_test());
}

#[test]
fn dead_holder_rejects_new_work_immediately_and_preserves_peer() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let manager = manager_with(&fake);
    let failed = create(&manager, &fake, "failed", FinalizePolicy::NoOp);
    let peer = create(&manager, &fake, "peer", FinalizePolicy::NoOp);

    failed.handle.mark_holder_exited_for_test("signal:9");

    let error = manager
        .with_gated_session(&failed.workspace_session_id, |_| ())
        .expect_err("dead holder is unavailable before cleanup starts");
    assert!(matches!(
        error,
        WorkspaceSessionError::HolderExited { workspace_session_id, .. }
            if workspace_session_id == failed.workspace_session_id
    ));
    assert!(manager
        .with_gated_session(&peer.workspace_session_id, |_| ())
        .is_ok());
    assert!(fake.destroy_calls().is_empty());
}

#[test]
fn no_op_holder_exit_cleans_once_and_emits_one_terminal_result() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let manager = manager_with(&fake);
    let failed = create(&manager, &fake, "failed", FinalizePolicy::NoOp);
    failed.handle.mark_holder_exited_for_test("signal:9");

    let first = manager.reconcile_holder_exits();
    let second = manager.reconcile_holder_exits();

    assert!(matches!(
        first.as_slice(),
        [outcome] if outcome.disposition == HolderExitDisposition::Destroyed
    ));
    assert!(second.is_empty());
    assert_eq!(
        fake.destroy_calls(),
        vec![failed.workspace_session_id.clone()]
    );
    let lifecycle = manager.holder_lifecycle_snapshot();
    assert_eq!(lifecycle.holder_exit_total, 1);
    assert_eq!(lifecycle.cleanup_attempt_total, 1);
    assert_eq!(lifecycle.cleanup_failure_total, 0);
    assert_eq!(lifecycle.cleanup_terminal_total, 1);
    assert_eq!(lifecycle.events.len(), 3);
}

#[test]
fn holder_exit_cancels_and_joins_live_commands_before_destroy() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let launch_driver = Arc::new(support::FakeLaunchDriver::new());
    let services =
        support::build_services_with_launch_driver(Arc::clone(&fake), Arc::clone(&launch_driver));
    let failed = create(
        &services.workspace,
        &fake,
        "command-owner",
        FinalizePolicy::NoOp,
    );
    let running = services
        .command
        .exec_command(ExecCommandInput {
            workspace_session_id: Some(failed.workspace_session_id.clone()),
            cmd: "sleep 60".to_owned(),
            timeout_ms: None,
            yield_time_ms: Some(0),
        })
        .expect("command is admitted and remains live");
    let command_id = running
        .command_session_id
        .expect("running response carries command id");

    failed.handle.mark_holder_exited_for_test("signal:9");
    let outcomes = services.workspace.reconcile_holder_exits();

    assert!(matches!(
        outcomes.as_slice(),
        [outcome] if outcome.disposition == HolderExitDisposition::Destroyed
    ));
    assert_eq!(
        launch_driver.launcher().recorded_cancel_request_ids(),
        vec![command_id.0]
    );
    assert!(services.command.active_namespace_executions().is_empty());
    assert_eq!(fake.destroy_calls(), vec![failed.workspace_session_id]);
}

#[test]
fn command_join_timeout_preserves_ledger_and_resources_for_retry() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let launch_driver = Arc::new(support::FakeLaunchDriver::new());
    launch_driver
        .launcher()
        .push_script(support::FakeRunnerScript::pending_ignoring_cancel());
    let services =
        support::build_services_with_launch_driver(Arc::clone(&fake), Arc::clone(&launch_driver));
    let failed = create(
        &services.workspace,
        &fake,
        "command-timeout",
        FinalizePolicy::NoOp,
    );
    let running = services
        .command
        .exec_command(ExecCommandInput {
            workspace_session_id: Some(failed.workspace_session_id.clone()),
            cmd: "sleep 60".to_owned(),
            timeout_ms: None,
            yield_time_ms: Some(0),
        })
        .expect("command is admitted and remains live");
    let command_id = running
        .command_session_id
        .expect("running response carries command id");

    failed.handle.mark_holder_exited_for_test("signal:9");
    let started = Instant::now();
    let first = services.workspace.reconcile_holder_exits();

    assert!(matches!(
        first.as_slice(),
        [outcome]
            if matches!(
                &outcome.disposition,
                HolderExitDisposition::RetryableCleanupFailure { diagnostic }
                    if diagnostic.contains("timed out joining command")
            )
    ));
    assert!(started.elapsed() >= Duration::from_secs(1));
    assert!(started.elapsed() < Duration::from_secs(2));
    assert_eq!(
        services
            .workspace
            .finalization_state_for_test(&failed.workspace_session_id),
        Some(FinalizationState::FinalizeFailed)
    );
    assert_eq!(
        services
            .command
            .active_namespace_executions()
            .into_iter()
            .map(|execution| execution.namespace_execution_id)
            .collect::<Vec<_>>(),
        vec![command_id.clone()],
        "the admitted command ledger remains owned after a failed join"
    );
    assert!(
        fake.destroy_calls().is_empty(),
        "resources remain for retry"
    );

    launch_driver.launcher().complete_request(
        &command_id.0,
        sandbox_runtime_namespace_process::runner::protocol::RunResult {
            exit_code: 0,
            payload: serde_json::json!({ "status": "cancelled" }),
        },
    );
    wait_until(Duration::from_secs(1), || {
        services.command.active_namespace_executions().is_empty()
    });
    let second = services.workspace.reconcile_holder_exits();
    assert!(matches!(
        second.as_slice(),
        [outcome] if outcome.disposition == HolderExitDisposition::Destroyed
    ));
    assert_eq!(fake.destroy_calls(), vec![failed.workspace_session_id]);
}

#[test]
fn holder_cleanup_failure_remains_retryable_without_recounting_exit() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let manager = manager_with(&fake);
    let failed = create(&manager, &fake, "retry-cleanup", FinalizePolicy::NoOp);
    fake.push_destroy_result(Err(sandbox_runtime_workspace::WorkspaceError::Cleanup {
        workspace_session_id: failed.workspace_session_id.0.clone(),
        failures: vec!["Network: injected failure".to_owned()],
    }));
    failed.handle.mark_holder_exited_for_test("signal:9");

    let first = manager.reconcile_holder_exits();
    let second = manager.reconcile_holder_exits();

    assert!(matches!(
        first.as_slice(),
        [outcome]
            if matches!(
                outcome.disposition,
                HolderExitDisposition::RetryableCleanupFailure { .. }
            )
    ));
    assert!(matches!(
        second.as_slice(),
        [outcome] if outcome.disposition == HolderExitDisposition::Destroyed
    ));
    assert_eq!(
        fake.destroy_calls(),
        vec![
            failed.workspace_session_id.clone(),
            failed.workspace_session_id.clone(),
        ]
    );
    let lifecycle = manager.holder_lifecycle_snapshot();
    assert_eq!(lifecycle.holder_exit_total, 1);
    assert_eq!(lifecycle.cleanup_attempt_total, 2);
    assert_eq!(lifecycle.cleanup_failure_total, 1);
    assert_eq!(lifecycle.cleanup_terminal_total, 1);
    assert_eq!(lifecycle.events.len(), 5);
}

#[test]
fn concurrent_explicit_destroy_callers_join_one_transaction() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let manager = Arc::new(manager_with(&fake));
    let handler = create(&manager, &fake, "racing", FinalizePolicy::NoOp);
    let (entered, release) = fake.park_next_destroy();

    let left_manager = Arc::clone(&manager);
    let left_handler = handler.clone();
    let left =
        std::thread::spawn(move || left_manager.destroy_session(left_handler, Default::default()));
    entered
        .recv_timeout(Duration::from_secs(1))
        .expect("first destroy enters runtime");

    let right_manager = Arc::clone(&manager);
    let right_handler = handler.clone();
    let right = std::thread::spawn(move || {
        right_manager.destroy_session(right_handler, Default::default())
    });
    std::thread::sleep(Duration::from_millis(20));
    release.send(()).expect("release first destroy");

    let left = left.join().expect("left joins").expect("left succeeds");
    let right = right.join().expect("right joins").expect("right succeeds");
    assert_eq!(left, right);
    assert_eq!(fake.destroy_calls(), vec![handler.workspace_session_id]);
}

#[test]
fn publish_required_holder_exit_commits_bounded_recovery_then_releases_workspace() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let manager = manager_with(&fake);
    let failed = create(
        &manager,
        &fake,
        "publish-required",
        FinalizePolicy::PublishThenDestroy,
    );
    failed.handle.mark_holder_exited_for_test("signal:9");

    let outcomes = manager.reconcile_holder_exits();

    let artifact = match outcomes.as_slice() {
        [outcome] => match &outcome.disposition {
            HolderExitDisposition::RecoveryRequired { artifact } => artifact,
            disposition => panic!("unexpected disposition: {disposition:?}"),
        },
        outcomes => panic!("unexpected outcomes: {outcomes:?}"),
    };
    let manifest: serde_json::Value = serde_json::from_slice(
        &std::fs::read(artifact.join("manifest.json")).expect("durable recovery manifest"),
    )
    .expect("recovery manifest json");
    assert_eq!(manifest["workspace_session_id"], "publish-required");
    assert_eq!(manifest["finalization_state"], "finalization_failed");
    assert!(manifest["artifact_max_bytes"].as_u64().unwrap() <= 1024 * 1024);
    assert!(fake.capture_calls().is_empty());
    assert_eq!(
        fake.destroy_calls(),
        vec![failed.workspace_session_id.clone()]
    );
    assert_eq!(
        manager.finalization_state_for_test(&WorkspaceSessionId("publish-required".to_owned())),
        None
    );
}
