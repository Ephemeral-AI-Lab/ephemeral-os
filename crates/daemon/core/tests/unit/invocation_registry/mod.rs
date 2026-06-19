use std::future;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use super::{InFlightRegistry, InvocationCancelResult};
use tokio::task::JoinHandle;

type TestResult = Result<(), Box<dyn std::error::Error + Send + Sync>>;

#[tokio::test]
async fn cancel_heartbeat_and_count_track_invocation() -> TestResult {
    let registry = InFlightRegistry::new(300.0, 30.0);
    let task = tokio::spawn(future::pending::<()>());
    registry.register("invocation-1", task.abort_handle(), "caller-a");

    assert_eq!(registry.count_by_caller("caller-a"), 1);
    assert_eq!(
        registry.heartbeat(&["invocation-1".to_owned(), "missing".to_owned()]),
        1
    );
    assert!(registry.cancel("invocation-1"));
    assert_task_cancelled(task).await?;
    assert_eq!(registry.count_by_caller("caller-a"), 0);

    registry.deregister("invocation-1");
    assert!(!registry.contains("invocation-1"));
    Ok(())
}

#[tokio::test]
async fn control_paths_recover_poisoned_registry_lock() -> TestResult {
    let registry = Arc::new(InFlightRegistry::new(300.0, 30.0));
    let poisoned = registry.clone();
    let poison_result = thread::spawn(move || {
        let _guard = match poisoned.inner.lock() {
            Ok(guard) => guard,
            Err(error) => error.into_inner(),
        };
        std::panic::resume_unwind(Box::new("poison in-flight registry"));
    })
    .join();
    if poison_result.is_ok() {
        return Err("poison helper thread completed without unwinding".into());
    }

    let task = tokio::spawn(future::pending::<()>());
    registry.register("poisoned", task.abort_handle(), "caller-a");

    assert_eq!(registry.count_by_caller("caller-a"), 1);
    assert_eq!(registry.heartbeat(&["poisoned".to_owned()]), 1);
    registry.ttl_sweep();
    assert!(registry.cancel("poisoned"));
    assert_task_cancelled(task).await?;
    registry.deregister("poisoned");
    assert!(!registry.contains("poisoned"));
    Ok(())
}

#[tokio::test]
async fn ttl_sweep_reaps_idle_invocation() -> TestResult {
    let registry = InFlightRegistry::new(0.001, 30.0);
    let task = tokio::spawn(future::pending::<()>());
    registry.register("ttl", task.abort_handle(), "caller-a");

    thread::sleep(Duration::from_millis(3));
    registry.ttl_sweep();
    assert!(registry.contains("ttl"));
    assert_eq!(registry.count_by_caller("caller-a"), 0);

    assert_task_cancelled(task).await?;
    assert_eq!(registry.count_by_caller("caller-a"), 0);
    Ok(())
}

#[tokio::test]
async fn started_blocking_invocation_reports_uncancellable() -> TestResult {
    let registry = InFlightRegistry::new(300.0, 30.0);
    let task = tokio::spawn(future::pending::<()>());
    registry.register_blocking(
        "blocking-running",
        task.abort_handle(),
        Arc::new(AtomicBool::new(true)),
        "caller-a",
    );

    assert_eq!(
        registry.cancel_invocation("blocking-running"),
        InvocationCancelResult::RunningUncancellable
    );
    assert_eq!(registry.count_by_caller("caller-a"), 1);

    task.abort();
    assert_task_cancelled(task).await?;
    registry.deregister("blocking-running");
    Ok(())
}

#[tokio::test]
async fn ttl_sweep_hides_started_blocking_invocation() -> TestResult {
    let registry = InFlightRegistry::new(0.001, 30.0);
    let task = tokio::spawn(future::pending::<()>());
    registry.register_blocking(
        "blocking-ttl",
        task.abort_handle(),
        Arc::new(AtomicBool::new(true)),
        "caller-a",
    );

    assert_eq!(registry.count_by_caller("caller-a"), 1);
    thread::sleep(Duration::from_millis(3));
    registry.ttl_sweep();
    assert!(registry.contains("blocking-ttl"));
    assert_eq!(registry.count_by_caller("caller-a"), 0);
    assert_eq!(registry.heartbeat(&["blocking-ttl".to_owned()]), 0);

    task.abort();
    assert_task_cancelled(task).await?;
    registry.deregister("blocking-ttl");
    Ok(())
}

async fn assert_task_cancelled(task: JoinHandle<()>) -> TestResult {
    match task.await {
        Ok(()) => Err("expected task cancellation, but task completed".into()),
        Err(error) if error.is_cancelled() => Ok(()),
        Err(error) => Err(format!("expected task cancellation, got {error}").into()),
    }
}
