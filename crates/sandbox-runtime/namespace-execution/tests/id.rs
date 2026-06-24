use std::collections::HashSet;

use sandbox_runtime_namespace_execution::NamespaceExecutionId;

#[test]
fn newtype_exposes_inner_and_is_hashable() {
    let id = NamespaceExecutionId("namespace_execution_1".to_owned());
    assert_eq!(id.0, "namespace_execution_1");
    let mut set = HashSet::new();
    assert!(set.insert(id.clone()));
    assert!(!set.insert(id)); // Eq + Hash round-trip
}
