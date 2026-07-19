mod support;

mod workspace_crate {
    pub use sandbox_runtime_workspace::WorkspaceSessionId;
}

mod command_teardown_logic {
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/src/command/service/teardown.rs"
    ));
}

use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use sandbox_runtime::command::{CommandServiceError, ExecCommandInput};
use sandbox_runtime::workspace_session::{
    FinalizePolicy, HolderExitDisposition, WorkspaceSessionService,
};
use sandbox_runtime_workspace::{NetworkProfile, WorkspaceSessionId};

use command_teardown_logic::{
    cancel_and_join_commands, CommandTeardownFailure, CommandTeardownTarget,
};
use sandbox_runtime_namespace_execution::{CompletionWaiter, NamespaceExecutionId};

use support::{FakeLaunchDriver, FakeRunnerScript, FakeWorkspaceService};

struct RecordingWaiter {
    resolved: bool,
    waits: Arc<AtomicUsize>,
    cancellations: Arc<AtomicUsize>,
    cancellations_seen: Arc<AtomicUsize>,
}

impl CompletionWaiter for RecordingWaiter {
    fn wait_timeout(&self, timeout: Duration) -> bool {
        self.waits.fetch_add(1, Ordering::SeqCst);
        self.cancellations_seen
            .store(self.cancellations.load(Ordering::SeqCst), Ordering::SeqCst);
        if !self.resolved {
            std::thread::sleep(timeout);
        }
        self.resolved
    }
}

fn id(value: &str) -> NamespaceExecutionId {
    NamespaceExecutionId(value.to_owned())
}

fn create_workspace(
    workspace: &WorkspaceSessionService,
    fake: &FakeWorkspaceService,
    workspace_session_id: &str,
) -> sandbox_runtime::workspace_session::WorkspaceSessionHandler {
    fake.push_create_result(Ok(support::workspace_handle(
        workspace_session_id,
        &format!("lease-{workspace_session_id}"),
        PathBuf::from("/workspace"),
        NetworkProfile::Shared,
    )));
    workspace
        .create_workspace_session(support::create_request_with_policy(FinalizePolicy::NoOp))
        .expect("workspace creation succeeds")
}

fn start_command(
    services: &support::TestServices,
    workspace_session_id: &WorkspaceSessionId,
    command: &str,
) -> sandbox_runtime::NamespaceExecutionId {
    services
        .command
        .exec_command(ExecCommandInput {
            workspace_session_id: Some(workspace_session_id.clone()),
            cmd: command.to_owned(),
            timeout_ms: None,
            yield_time_ms: Some(0),
        })
        .expect("command is admitted")
        .command_session_id
        .expect("running command has an execution id")
}

fn wait_until(timeout: Duration, predicate: impl Fn() -> bool) {
    let deadline = std::time::Instant::now() + timeout;
    while std::time::Instant::now() < deadline {
        if predicate() {
            return;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    assert!(predicate(), "condition did not converge within {timeout:?}");
}

#[test]
fn timeout_join_does_not_prevent_cancelling_later_workspace_commands() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let launch_driver = Arc::new(FakeLaunchDriver::new());
    launch_driver
        .launcher()
        .push_script(FakeRunnerScript::pending_ignoring_cancel());
    launch_driver
        .launcher()
        .push_script(FakeRunnerScript::pending());
    let services =
        support::build_services_with_launch_driver(Arc::clone(&fake), Arc::clone(&launch_driver));
    let workspace = create_workspace(&services.workspace, &fake, "timeout-before-peer");
    let first = start_command(
        &services,
        &workspace.workspace_session_id,
        "sleep 600 # first",
    );
    let second = start_command(
        &services,
        &workspace.workspace_session_id,
        "sleep 600 # second",
    );

    fake.mark_holder_exited(&workspace.handle, "signal:9");
    let outcomes = services.workspace.reconcile_holder_exits();
    let cancelled = launch_driver.launcher().recorded_cancel_request_ids();

    launch_driver.launcher().complete_request(
        &first.0,
        sandbox_runtime_namespace_process::runner::protocol::RunResult {
            exit_code: 130,
            payload: serde_json::json!({ "status": "cancelled" }),
        },
    );
    launch_driver.launcher().complete_request(
        &second.0,
        sandbox_runtime_namespace_process::runner::protocol::RunResult {
            exit_code: 130,
            payload: serde_json::json!({ "status": "cancelled" }),
        },
    );
    wait_until(Duration::from_secs(1), || {
        services.command.active_namespace_executions().is_empty()
    });

    assert!(matches!(
        outcomes.as_slice(),
        [outcome]
            if matches!(
                &outcome.disposition,
                HolderExitDisposition::RetryableCleanupFailure { diagnostic }
                    if diagnostic.contains(&first.0)
            )
    ));
    assert_eq!(cancelled, vec![first.0, second.0]);
}

#[test]
fn command_service_shutdown_rejects_late_work_without_a_runner_spawn() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let launch_driver = Arc::new(FakeLaunchDriver::new());
    let services =
        support::build_services_with_launch_driver(Arc::clone(&fake), Arc::clone(&launch_driver));
    let workspace = create_workspace(&services.workspace, &fake, "command-shutdown");

    services
        .command
        .shutdown_and_join()
        .expect("empty command engine shuts down");
    services
        .command
        .shutdown_and_join()
        .expect("command shutdown is idempotent");
    let error = services
        .command
        .exec_command(ExecCommandInput {
            workspace_session_id: Some(workspace.workspace_session_id),
            cmd: "echo must-not-spawn".to_owned(),
            timeout_ms: None,
            yield_time_ms: Some(0),
        })
        .expect_err("command after shutdown is rejected");

    assert!(matches!(
        error,
        CommandServiceError::CommandIo { error, .. }
            if error.contains("namespace execution engine is shut down")
    ));
    assert!(launch_driver.recorded_request_ids().is_empty());
    assert!(services.command.active_namespace_executions().is_empty());
}

#[test]
fn missing_mismatched_and_timed_out_commands_are_aggregated_without_skipping_peers() {
    assert_eq!(
        command_teardown_logic::COMMAND_JOIN_TIMEOUT,
        Duration::from_secs(1)
    );
    let expected_workspace = WorkspaceSessionId("expected-workspace".to_owned());
    let other_workspace = WorkspaceSessionId("other-workspace".to_owned());
    let missing = id("missing");
    let mismatched = id("mismatched");
    let timed_out = id("timed-out");
    let peer = id("peer");
    let eligible_cancellations = Arc::new(AtomicUsize::new(0));
    let mismatched_cancellations = Arc::new(AtomicUsize::new(0));
    let waits = Arc::new(AtomicUsize::new(0));
    let cancellations_seen = Arc::new(AtomicUsize::new(0));
    let started = std::time::Instant::now();

    let error = cancel_and_join_commands(
        &expected_workspace,
        &[missing.clone(), mismatched.clone(), timed_out.clone(), peer],
        Duration::from_millis(20),
        |command_id| match command_id.0.as_str() {
            "missing" => None,
            "mismatched" => {
                let cancellations = Arc::clone(&mismatched_cancellations);
                Some(CommandTeardownTarget {
                    owner: other_workspace.clone(),
                    cancel: Arc::new(move || {
                        cancellations.fetch_add(1, Ordering::SeqCst);
                    }),
                    completion: Arc::new(RecordingWaiter {
                        resolved: true,
                        waits: Arc::clone(&waits),
                        cancellations: Arc::clone(&eligible_cancellations),
                        cancellations_seen: Arc::clone(&cancellations_seen),
                    }),
                })
            }
            "timed-out" | "peer" => {
                let cancellations = Arc::clone(&eligible_cancellations);
                Some(CommandTeardownTarget {
                    owner: expected_workspace.clone(),
                    cancel: Arc::new(move || {
                        cancellations.fetch_add(1, Ordering::SeqCst);
                    }),
                    completion: Arc::new(RecordingWaiter {
                        resolved: command_id.0 == "peer",
                        waits: Arc::clone(&waits),
                        cancellations: Arc::clone(&eligible_cancellations),
                        cancellations_seen: Arc::clone(&cancellations_seen),
                    }),
                })
            }
            unexpected => panic!("unexpected command id {unexpected}"),
        },
    )
    .expect_err("all teardown failures are returned together");

    assert!(started.elapsed() < Duration::from_millis(250));
    assert_eq!(eligible_cancellations.load(Ordering::SeqCst), 2);
    assert_eq!(mismatched_cancellations.load(Ordering::SeqCst), 0);
    assert_eq!(waits.load(Ordering::SeqCst), 2);
    assert_eq!(cancellations_seen.load(Ordering::SeqCst), 2);
    assert_eq!(
        error.failures,
        vec![
            CommandTeardownFailure::MissingExecutionHandle {
                command_id: missing,
            },
            CommandTeardownFailure::WorkspaceMismatch {
                command_id: mismatched,
                actual_workspace_id: other_workspace,
                expected_workspace_id: expected_workspace,
            },
            CommandTeardownFailure::JoinTimedOut {
                command_id: timed_out,
            },
        ]
    );
}
