//! Behavioral coverage of the engine dispatch + watcher against the fake
//! launcher — the authoritative Phase 2 signal (runs on darwin; no real fork).

use std::sync::Arc;

use sandbox_runtime_namespace_execution::test_support::{
    run_result, sample_target, ErrShellOp, FakeLauncher, FakeObserver, ObserverEvent, OkShellOp,
};
use sandbox_runtime_namespace_execution::{
    NamespaceExecutionEngine, NamespaceExecutionError, NamespaceExecutionId,
    NamespaceExecutionTerminalStatus,
};

fn id(suffix: &str) -> NamespaceExecutionId {
    NamespaceExecutionId(format!("namespace_execution_{suffix}"))
}

#[test]
fn shell_execution_resolves_finalized_output_and_records_terminal() {
    let fake = FakeLauncher::new();
    let observer = Arc::new(FakeObserver::new());
    let engine =
        NamespaceExecutionEngine::with_launcher(Box::new(fake.clone()), observer.clone(), 4);
    let id = id("ok");

    let exec = engine
        .run_shell_interactive(OkShellOp, sample_target(), id.clone())
        .expect("shell admitted");
    assert_eq!(exec.id().0, id.0);

    fake.complete_latest(run_result(0, "ok"));
    assert_eq!(exec.wait().expect("resolved Ok"), 0);

    let (status, exit_code) = observer.await_terminal();
    assert_eq!(status, NamespaceExecutionTerminalStatus::Ok);
    assert_eq!(exit_code, Some(0));
    assert_eq!(observer.events().first(), Some(&ObserverEvent::Running(id)));
}

#[test]
fn shell_finalize_error_resolves_terminal_error() {
    let fake = FakeLauncher::new();
    let observer = Arc::new(FakeObserver::new());
    let engine =
        NamespaceExecutionEngine::with_launcher(Box::new(fake.clone()), observer.clone(), 4);

    let exec = engine
        .run_shell_interactive(ErrShellOp, sample_target(), id("finalize_err"))
        .expect("admitted");
    fake.complete_latest(run_result(0, "ok"));

    let error = exec.wait().expect_err("finalize error surfaced");
    assert!(matches!(error, NamespaceExecutionError::Finalize(_)));
    let (status, _exit) = observer.await_terminal();
    assert_eq!(status, NamespaceExecutionTerminalStatus::Error);
}

#[test]
fn cancel_unblocks_the_blocked_watcher() {
    let fake = FakeLauncher::new();
    let observer = Arc::new(FakeObserver::new());
    let engine =
        NamespaceExecutionEngine::with_launcher(Box::new(fake.clone()), observer.clone(), 4);

    let exec = engine
        .run_shell_interactive(OkShellOp, sample_target(), id("cancel"))
        .expect("admitted");

    // The watcher is blocked in wait_completion; cancel trips the fake completion
    // (a real concurrent unblock), and the promise resolves promptly.
    exec.cancel();
    assert_eq!(exec.wait().expect("resolved after cancel"), 130);
    let (status, _exit) = observer.await_terminal();
    assert_eq!(status, NamespaceExecutionTerminalStatus::Cancelled);
}

#[test]
fn admission_refuses_when_full_then_readmits_after_completion() {
    let fake = FakeLauncher::new();
    let observer = Arc::new(FakeObserver::new());
    let engine = NamespaceExecutionEngine::with_launcher(Box::new(fake.clone()), observer, 1);

    let first = engine
        .run_shell_interactive(OkShellOp, sample_target(), id("1"))
        .expect("first admitted");
    let refused = engine
        .run_shell_interactive(OkShellOp, sample_target(), id("2"))
        .err()
        .expect("second refused while full");
    assert!(matches!(
        refused,
        NamespaceExecutionError::Admission { max_active: 1 }
    ));

    // complete-before-resolve ⟹ the slot is freed by the time wait() returns.
    fake.complete_latest(run_result(0, "ok"));
    assert_eq!(first.wait().expect("first resolved"), 0);

    let third = engine
        .run_shell_interactive(OkShellOp, sample_target(), id("3"))
        .expect("readmitted after completion");
    fake.complete_latest(run_result(0, "ok"));
    assert_eq!(third.wait().expect("third resolved"), 0);
}

#[test]
fn mount_execution_resolves_parsed_output() {
    let fake = FakeLauncher::new();
    let observer = Arc::new(FakeObserver::new());
    let engine =
        NamespaceExecutionEngine::with_launcher(Box::new(fake.clone()), observer.clone(), 4);
    let id = id("mount");

    let handle = engine
        .run_mount("--mount-overlay", sample_target(), id.clone(), |outcome| {
            Ok(outcome.exit_code())
        })
        .expect("mount admitted");
    assert_eq!(handle.id().0, id.0);

    fake.complete_latest(run_result(7, "ok"));
    assert_eq!(handle.wait().expect("mount resolved"), 7);
    let (status, exit_code) = observer.await_terminal();
    assert_eq!(status, NamespaceExecutionTerminalStatus::Ok);
    assert_eq!(exit_code, Some(7));
}

#[test]
fn mount_parse_error_resolves_terminal_error() {
    let fake = FakeLauncher::new();
    let observer = Arc::new(FakeObserver::new());
    let engine =
        NamespaceExecutionEngine::with_launcher(Box::new(fake.clone()), observer.clone(), 4);

    let handle = engine
        .run_mount(
            "--remount-overlay",
            sample_target(),
            id("mount_err"),
            |_outcome| Err::<i64, _>(NamespaceExecutionError::Finalize("bad probe".to_owned())),
        )
        .expect("admitted");
    fake.complete_latest(run_result(0, "ok"));

    let error = handle.wait().expect_err("parse error surfaced");
    assert!(matches!(error, NamespaceExecutionError::Finalize(_)));
    let (status, _exit) = observer.await_terminal();
    assert_eq!(status, NamespaceExecutionTerminalStatus::Error);
}

#[test]
fn namespace_execution_id_is_the_runner_request_id() {
    let fake = FakeLauncher::new();
    let observer = Arc::new(FakeObserver::new());
    let engine = NamespaceExecutionEngine::with_launcher(Box::new(fake.clone()), observer, 4);
    let id = id("42");

    let exec = engine
        .run_shell_interactive(OkShellOp, sample_target(), id.clone())
        .expect("admitted");
    assert_eq!(exec.id().0, id.0);
    assert_eq!(fake.recorded_request_ids(), vec![id.0.clone()]);

    fake.complete_latest(run_result(0, "ok"));
    let _ = exec.wait();
}
