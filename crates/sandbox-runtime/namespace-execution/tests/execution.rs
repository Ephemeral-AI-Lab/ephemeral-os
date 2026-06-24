use sandbox_runtime_namespace_execution::test_support::CompletionPromise;
use sandbox_runtime_namespace_execution::{
    ExecutionHandle, InteractiveExecution, NamespaceExecutionId,
};

#[test]
fn interactive_forwards_to_inner_handle() {
    let promise = CompletionPromise::<u32>::new();
    assert!(promise.resolve(Ok(7)));
    let handle = ExecutionHandle::new(
        NamespaceExecutionId("namespace_execution_1".to_owned()),
        promise,
    );
    let interactive = InteractiveExecution::new(handle);

    assert_eq!(interactive.id().0, "namespace_execution_1");
    assert_eq!(interactive.execution().id().0, "namespace_execution_1");
    assert!(interactive.is_finished());
    assert_eq!(interactive.wait().expect("resolved Ok"), 7);
}
