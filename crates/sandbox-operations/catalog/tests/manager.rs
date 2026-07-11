#![cfg(feature = "manager")]

use sandbox_operation_catalog::manager::manager_catalog;
use sandbox_operation_contract::{catalog_to_value, OperationDomain};

#[test]
fn management_catalog_is_the_exact_public_set() {
    let catalog = manager_catalog();
    let names = catalog
        .operations
        .iter()
        .map(|operation| operation.name)
        .collect::<Vec<_>>();

    assert_eq!(catalog.operation_execution_space, OperationDomain::Manager);
    assert_eq!(catalog.families.len(), 1);
    assert_eq!(catalog.families[0].id, "management");
    assert_eq!(
        names,
        [
            "create_sandbox",
            "list_docker_images",
            "list_workspace_directories",
            "destroy_sandbox",
            "list_sandboxes",
            "inspect_sandbox",
            "squash_layerstacks",
            "export_changes",
        ]
    );
    assert!(catalog
        .operations
        .iter()
        .all(|operation| operation.family == "management"));
    assert!(!names.contains(&"checkpoint_squash"));
    assert!(!names.contains(&"snapshot"));
    let serialized = catalog_to_value(catalog).to_string();
    assert!(!serialized.contains("checkpoint_squash"));
}

#[test]
fn squash_layerstacks_keeps_the_singular_daemon_operation_internal() {
    let operation = manager_catalog()
        .operations
        .iter()
        .find(|operation| operation.name == "squash_layerstacks")
        .expect("public squash operation");
    assert_eq!(operation.args.len(), 1);
    assert_eq!(operation.args[0].name, "sandbox_id");
    assert!(operation.args[0].required);
    assert!(operation.description.contains("squash_layerstack"));
    assert!(operation.related.contains(&"export_changes"));
}
