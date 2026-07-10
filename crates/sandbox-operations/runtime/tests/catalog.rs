use sandbox_protocol::{catalog_to_value, ArgKind, CliOperationExecutionSpace};

#[test]
fn runtime_catalog_is_the_exact_public_runtime_surface() {
    let catalog = sandbox_runtime_operations::runtime_catalog();

    assert_eq!(
        catalog.operation_execution_space,
        CliOperationExecutionSpace::Runtime
    );
    assert_eq!(
        catalog
            .families
            .iter()
            .map(|family| family.id)
            .collect::<Vec<_>>(),
        ["command", "file"]
    );
    assert_eq!(
        catalog
            .operations
            .iter()
            .map(|operation| operation.name)
            .collect::<Vec<_>>(),
        [
            "exec_command",
            "write_command_stdin",
            "read_command_lines",
            "file_read",
            "file_write",
            "file_edit",
            "file_blame",
        ]
    );
    assert!(catalog
        .operations
        .iter()
        .all(|operation| operation.cli.is_some()));
    let edits = catalog
        .operations
        .iter()
        .find(|operation| operation.name == "file_edit")
        .and_then(|operation| operation.args.iter().find(|arg| arg.name == "edits"))
        .expect("file_edit edits argument");
    assert_eq!(edits.kind, ArgKind::JsonArray);
    let encoded = catalog_to_value(catalog);
    let encoded_edits = encoded["operations"]
        .as_array()
        .and_then(|operations| {
            operations
                .iter()
                .find(|operation| operation["name"] == "file_edit")
        })
        .and_then(|operation| operation["args"].as_array())
        .and_then(|args| args.iter().find(|arg| arg["name"] == "edits"))
        .expect("encoded edits argument");
    assert_eq!(encoded_edits["kind"], "json_array");
}

#[test]
fn internal_runtime_operations_do_not_leak_into_the_public_catalog() {
    let encoded = catalog_to_value(sandbox_runtime_operations::runtime_catalog()).to_string();

    assert!(sandbox_runtime_operations::FILE_LIST_SPEC.cli.is_none());
    for internal in [
        "create_workspace_session",
        "destroy_workspace_session",
        "file_list",
    ] {
        assert!(
            !encoded.contains(internal),
            "internal operation {internal} leaked into the public catalog"
        );
    }
}
