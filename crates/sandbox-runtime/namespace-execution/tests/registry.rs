use sandbox_runtime_namespace_execution::test_support::ExecutionRegistry;

#[test]
fn reports_configured_capacity() {
    assert_eq!(ExecutionRegistry::new(2).max_active(), 2);
}
