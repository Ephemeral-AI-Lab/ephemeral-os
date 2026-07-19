//! Process-isolated coverage for namespace execution's bounded worker topology.

include!("support/namespace_execution_src.rs");

mod support;

use std::sync::Arc;
use std::time::{Duration, Instant};

use support::{sample_target, FakeLauncher, FakeObserver, OkShellOp};

fn id(suffix: &str) -> NamespaceExecutionId {
    NamespaceExecutionId(format!("namespace_execution_{suffix}"))
}

#[test]
fn concurrent_shells_share_bounded_daemon_lifetime_background_workers() {
    let fake = FakeLauncher::new();
    let engine = NamespaceExecutionEngine::with_launcher(
        Box::new(fake),
        Arc::new(FakeObserver::new()),
        ExecutionCaps {
            max_active: 32,
            setup_timeout_s: 30.0,
            ..ExecutionCaps::default()
        },
    );
    let initial = engine.background_worker_snapshot();
    assert_eq!(initial.completion_supervisor_threads, 1);
    assert_eq!(initial.pty_reactor_threads, 0);
    assert_eq!(initial.active_completions, 0);
    assert_eq!(initial.active_pty_readers, 0);

    let executions = (0..32)
        .map(|index| {
            engine
                .run_shell_interactive(
                    OkShellOp,
                    sample_target(),
                    id(&format!("bounded_{index}")),
                    |_| {},
                    None,
                    None,
                )
                .expect("shell admitted")
        })
        .collect::<Vec<_>>();
    let active = engine.background_worker_snapshot();
    assert_eq!(active.completion_supervisor_threads, 1);
    assert_eq!(active.pty_reactor_threads, 1);
    assert_eq!(active.active_completions, 32);
    assert_eq!(active.active_pty_readers, 32);

    for execution in &executions {
        (execution.cancel_handle())();
    }
    for execution in executions {
        assert_eq!(execution.wait().expect("cancelled shell reaped"), 130);
    }
    wait_for_workers_to_settle(&engine);
    let settled = engine.background_worker_snapshot();
    assert_eq!(settled.completion_supervisor_threads, 1);
    assert_eq!(settled.active_completions, 0);
    assert_eq!(settled.active_pty_readers, 0);

    let restarted = engine
        .run_shell_interactive(
            OkShellOp,
            sample_target(),
            id("bounded_restart"),
            |_| {},
            None,
            None,
        )
        .expect("completion owner accepts work after idle");
    let active_again = engine.background_worker_snapshot();
    assert_eq!(active_again.completion_supervisor_threads, 1);
    assert_eq!(active_again.active_completions, 1);
    (restarted.cancel_handle())();
    assert_eq!(restarted.wait().expect("restarted shell reaped"), 130);
    wait_for_workers_to_settle(&engine);
    assert_eq!(
        engine
            .background_worker_snapshot()
            .completion_supervisor_threads,
        1
    );
}

fn wait_for_workers_to_settle(engine: &NamespaceExecutionEngine) {
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        let snapshot = engine.background_worker_snapshot();
        if snapshot.active_completions == 0 && snapshot.active_pty_readers == 0 {
            return;
        }
        if Instant::now() >= deadline {
            assert_eq!(
                snapshot.active_completions, 0,
                "completion owner converges after every child exits"
            );
            assert_eq!(
                snapshot.active_pty_readers, 0,
                "PTY readers converge after every child exits"
            );
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}
