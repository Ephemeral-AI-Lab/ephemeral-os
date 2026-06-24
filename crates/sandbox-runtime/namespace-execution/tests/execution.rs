use std::sync::Arc;

include!("support/execution_src.rs");

use crate::promise::CompletionPromise;

#[test]
fn handle_reports_resolved_value() {
    let promise = Arc::new(CompletionPromise::<u32>::new());
    assert!(promise.resolve(Ok(7)));
    let handle = ExecutionHandle::new(
        NamespaceExecutionId("namespace_execution_1".to_owned()),
        promise,
    );

    assert_eq!(handle.id().0, "namespace_execution_1");
    assert!(handle.is_finished());
    assert_eq!(handle.wait().expect("resolved Ok"), 7);
}
