#![cfg(all(feature = "manager", feature = "runtime", feature = "observability"))]

use sandbox_cli::core::request_builder::{
    build_request_from_catalog_with_id, build_request_from_values,
    build_request_from_values_with_id, catalog_document, BuildRequestInput, BuildRequestValueInput,
};
use sandbox_protocol::CliOperationExecutionSpace;
use serde_json::json;

#[test]
fn value_and_argv_inputs_share_management_defaults_and_scope() {
    let catalog =
        catalog_document(sandbox_manager_operations::manager_catalog()).expect("manager catalog");
    let argv_request = build_request_from_catalog_with_id(
        BuildRequestInput {
            execution_space: CliOperationExecutionSpace::Manager,
            operation: "create_sandbox".to_owned(),
            operation_argv: vec![
                "--image".to_owned(),
                "ubuntu:24.04".to_owned(),
                "--workspace-bind-root".to_owned(),
                "/workspace".to_owned(),
            ],
            sandbox_id: None,
        },
        &catalog,
        "request-1",
    )
    .expect("argv request");
    let value_request = build_request_from_values_with_id(
        BuildRequestValueInput {
            execution_space: CliOperationExecutionSpace::Manager,
            operation: "create_sandbox".to_owned(),
            arguments: json!({
                "image": "ubuntu:24.04",
                "workspace_root": "/workspace"
            }),
        },
        &catalog,
        "request-1",
    )
    .expect("value request");

    assert_eq!(value_request, argv_request);
    assert_eq!(value_request.args["count"], 1);
    assert_eq!(
        value_request.scope,
        sandbox_protocol::CliOperationScope::system()
    );
}

#[test]
fn value_input_mints_a_uuid_request_id() {
    let catalog =
        catalog_document(sandbox_manager_operations::manager_catalog()).expect("manager catalog");
    let request = build_request_from_values(
        BuildRequestValueInput {
            execution_space: CliOperationExecutionSpace::Manager,
            operation: "list_sandboxes".to_owned(),
            arguments: json!({}),
        },
        &catalog,
    )
    .expect("value request");

    uuid::Uuid::parse_str(&request.request_id).expect("UUID request id");
}

#[test]
fn runtime_value_selector_is_required_and_removed_from_operation_args() {
    let catalog =
        catalog_document(sandbox_runtime_operations::runtime_catalog()).expect("runtime catalog");
    let request = build_request_from_values_with_id(
        BuildRequestValueInput {
            execution_space: CliOperationExecutionSpace::Runtime,
            operation: "read_command_lines".to_owned(),
            arguments: json!({
                "sandbox_id": "eos-runtime",
                "command_session_id": "cmd-1"
            }),
        },
        &catalog,
        "request-2",
    )
    .expect("runtime value request");

    assert_eq!(request.op, "read_command_lines");
    assert_eq!(request.request_id, "request-2");
    assert_eq!(
        request.scope,
        sandbox_protocol::CliOperationScope::sandbox("eos-runtime")
    );
    assert_eq!(
        request.args,
        json!({"command_session_id": "cmd-1", "start_offset": 0, "limit": 200})
    );
}

#[test]
fn argv_and_value_inputs_share_native_file_edit_arrays() {
    let catalog =
        catalog_document(sandbox_runtime_operations::runtime_catalog()).expect("runtime catalog");
    let argv_request = build_request_from_catalog_with_id(
        BuildRequestInput {
            execution_space: CliOperationExecutionSpace::Runtime,
            operation: "file_edit".to_owned(),
            operation_argv: vec![
                "--path".to_owned(),
                "notes.txt".to_owned(),
                "--edits".to_owned(),
                r#"[{"old_string":"draft","new_string":"final"}]"#.to_owned(),
            ],
            sandbox_id: Some("eos-runtime".to_owned()),
        },
        &catalog,
        "request-edit",
    )
    .expect("argv request");
    let value_request = build_request_from_values_with_id(
        BuildRequestValueInput {
            execution_space: CliOperationExecutionSpace::Runtime,
            operation: "file_edit".to_owned(),
            arguments: json!({
                "sandbox_id": "eos-runtime",
                "path": "notes.txt",
                "edits": [{"old_string": "draft", "new_string": "final"}]
            }),
        },
        &catalog,
        "request-edit",
    )
    .expect("value request");

    assert_eq!(value_request, argv_request);
    assert!(value_request.args["edits"].is_array());
}

#[test]
fn file_edit_requires_a_json_array_for_both_input_forms() {
    let catalog =
        catalog_document(sandbox_runtime_operations::runtime_catalog()).expect("runtime catalog");
    let argv_error = build_request_from_catalog_with_id(
        BuildRequestInput {
            execution_space: CliOperationExecutionSpace::Runtime,
            operation: "file_edit".to_owned(),
            operation_argv: vec![
                "--path".to_owned(),
                "notes.txt".to_owned(),
                "--edits".to_owned(),
                "{}".to_owned(),
            ],
            sandbox_id: Some("eos-runtime".to_owned()),
        },
        &catalog,
        "request-edit-argv-error",
    )
    .expect_err("object edits rejected");
    let value_error = build_request_from_values_with_id(
        BuildRequestValueInput {
            execution_space: CliOperationExecutionSpace::Runtime,
            operation: "file_edit".to_owned(),
            arguments: json!({
                "sandbox_id": "eos-runtime",
                "path": "notes.txt",
                "edits": "[]"
            }),
        },
        &catalog,
        "request-edit-value-error",
    )
    .expect_err("string edits rejected");

    assert_eq!(argv_error.message(), "--edits must be a JSON array");
    assert_eq!(value_error.message(), "edits must be a JSON array");
}

#[test]
fn observability_values_share_aggregate_and_scoped_translation() {
    let catalog = catalog_document(sandbox_observability_operations::observability_catalog())
        .expect("observability catalog");
    let aggregate = build_request_from_values_with_id(
        BuildRequestValueInput {
            execution_space: CliOperationExecutionSpace::Observability,
            operation: "snapshot".to_owned(),
            arguments: json!({}),
        },
        &catalog,
        "request-3",
    )
    .expect("aggregate request");
    let scoped = build_request_from_values_with_id(
        BuildRequestValueInput {
            execution_space: CliOperationExecutionSpace::Observability,
            operation: "trace".to_owned(),
            arguments: json!({"sandbox_id": "eos-observe"}),
        },
        &catalog,
        "request-4",
    )
    .expect("scoped request");

    assert_eq!(aggregate.op, "snapshot");
    assert_eq!(
        aggregate.scope,
        sandbox_protocol::CliOperationScope::system()
    );
    assert_eq!(aggregate.args, json!({}));
    assert_eq!(scoped.op, "get_observability");
    assert_eq!(
        scoped.scope,
        sandbox_protocol::CliOperationScope::sandbox("eos-observe")
    );
    assert_eq!(scoped.args, json!({"view": "trace", "trace_id": "last"}));
}

#[test]
fn value_errors_are_deterministic_invalid_request_envelopes() {
    let catalog =
        catalog_document(sandbox_runtime_operations::runtime_catalog()).expect("runtime catalog");
    let cases = [
        (
            json!({"sandbox_id": "eos-x", "cmd": "pwd", "request_id": "injected"}),
            "unknown argument for exec_command: request_id",
        ),
        (
            json!({"sandbox_id": "eos-x", "command_session_id": "cmd-1", "limit": -1}),
            "limit must be an unsigned integer",
        ),
        (
            json!({"cmd": "pwd"}),
            "sandbox_id is required for runtime operations",
        ),
    ];

    for (arguments, expected) in cases {
        let error = build_request_from_values_with_id(
            BuildRequestValueInput {
                execution_space: CliOperationExecutionSpace::Runtime,
                operation: if arguments.get("command_session_id").is_some() {
                    "read_command_lines".to_owned()
                } else {
                    "exec_command".to_owned()
                },
                arguments,
            },
            &catalog,
            "request-error",
        )
        .expect_err("invalid value request");

        assert_eq!(error.message(), expected);
        assert_eq!(
            error.to_error_envelope(),
            json!({
                "error": {
                    "kind": "invalid_request",
                    "message": expected,
                    "details": {}
                }
            })
        );
    }
}

#[test]
fn value_input_requires_an_object_and_selected_catalog_operation() {
    let catalog =
        catalog_document(sandbox_manager_operations::manager_catalog()).expect("manager catalog");
    for (operation, arguments, expected) in [
        (
            "list_sandboxes",
            json!([]),
            "arguments for list_sandboxes must be an object",
        ),
        ("exec_command", json!({}), "unknown operation: exec_command"),
        (
            "list_sandboxes",
            json!({"sandbox_id": "eos-injected"}),
            "unknown argument for list_sandboxes: sandbox_id",
        ),
    ] {
        let error = build_request_from_values_with_id(
            BuildRequestValueInput {
                execution_space: CliOperationExecutionSpace::Manager,
                operation: operation.to_owned(),
                arguments,
            },
            &catalog,
            "request-error",
        )
        .expect_err("invalid value request");
        assert_eq!(error.message(), expected);
    }
}
