use sandbox_observability_operations::{observability_catalog, SNAPSHOT_SPEC};
use sandbox_protocol::CliOperationExecutionSpace;

#[test]
fn observability_catalog_is_the_exact_public_set() {
    let catalog = observability_catalog();
    let names = catalog
        .operations
        .iter()
        .map(|operation| operation.name)
        .collect::<Vec<_>>();

    assert_eq!(
        catalog.operation_execution_space,
        CliOperationExecutionSpace::Observability
    );
    assert_eq!(catalog.families.len(), 1);
    assert_eq!(catalog.families[0].id, "observability");
    assert_eq!(
        names,
        ["snapshot", "trace", "events", "cgroup", "layerstack"]
    );
    assert!(catalog.operations.iter().all(|operation| {
        operation.family == "observability"
            && operation.cli.is_some_and(|cli| {
                cli.usage.starts_with("sandbox-observability-cli ")
                    && cli
                        .examples
                        .iter()
                        .all(|example| example.starts_with("sandbox-observability-cli "))
            })
    }));
    let serialized = sandbox_protocol::catalog_to_value(catalog).to_string();
    assert!(!serialized.contains("sandbox-manager-cli observability"));
}

#[test]
fn snapshot_is_canonical_and_only_aggregate_capable_operation() {
    let catalog = observability_catalog();
    assert!(std::ptr::eq(catalog.operations[0], &SNAPSHOT_SPEC));

    for operation in catalog.operations {
        let sandbox_id = operation
            .args
            .iter()
            .find(|argument| argument.name == "sandbox_id")
            .expect("observability sandbox selector");
        assert_eq!(
            sandbox_id.required,
            operation.name != "snapshot",
            "only snapshot supports aggregate routing"
        );
    }
}

#[test]
fn public_operation_sets_are_pairwise_disjoint() {
    let catalogs = [
        ("management", sandbox_manager_operations::manager_catalog()),
        ("runtime", sandbox_runtime_operations::runtime_catalog()),
        ("observability", observability_catalog()),
    ];

    for (left_index, (left_name, left)) in catalogs.iter().enumerate() {
        for (right_name, right) in &catalogs[left_index + 1..] {
            for operation in left.operations {
                assert!(
                    !right
                        .operations
                        .iter()
                        .any(|candidate| candidate.name == operation.name),
                    "{} appears in both {left_name} and {right_name}",
                    operation.name
                );
            }
        }
    }
}
