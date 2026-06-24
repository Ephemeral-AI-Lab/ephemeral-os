use std::sync::Arc;
use std::thread;
use std::time::Duration;

use sandbox_runtime_namespace_execution::test_support::CompletionPromise;

#[test]
fn resolve_then_wait_yields_value() {
    let promise = CompletionPromise::<u32>::new();
    assert!(!promise.is_resolved()); // fresh cell is pending
    assert!(promise.resolve(Ok(42)));
    assert!(promise.is_resolved());
    assert!(!promise.resolve(Ok(7))); // second resolve is rejected
    assert_eq!(promise.wait().expect("resolved Ok"), 42);
}

#[test]
fn wait_timeout_blocks_until_resolved_from_another_thread() {
    let promise = Arc::new(CompletionPromise::<u32>::new());
    let writer = Arc::clone(&promise);
    let handle = thread::spawn(move || {
        thread::sleep(Duration::from_millis(50));
        writer.resolve(Ok(1));
    });

    // Blocks (no poll) until the other thread resolves, then reports true.
    assert!(promise.wait_timeout(Duration::from_secs(5)));
    handle.join().expect("writer thread");
    assert_eq!(promise.wait().expect("resolved"), 1);
}

#[test]
fn wait_timeout_returns_false_while_pending() {
    let promise = CompletionPromise::<u32>::new();
    assert!(!promise.wait_timeout(Duration::from_millis(50)));
}
