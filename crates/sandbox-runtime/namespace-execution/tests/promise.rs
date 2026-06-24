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
