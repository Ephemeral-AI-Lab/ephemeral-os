#![cfg(all(feature = "manager", feature = "runtime", feature = "observability"))]

use sandbox_cli::core::request_builder::{build_request_from_catalog_with_id, BuildRequestInput};
use sandbox_cli::projection::document::{catalog_document, CatalogDocument};
use sandbox_operation_client::{build_request_from_values_with_id, BuildRequestValueInput};
use sandbox_operation_contract::{OperationDomain, OperationScope, OperationSpecDocument};
use serde_json::json;

fn manager_catalog_document() -> CatalogDocument {
    catalog_document(
        sandbox_operation_catalog::manager::manager_catalog(),
        sandbox_cli::projection::manager::catalog_projection(),
    )
    .expect("manager catalog")
}

fn runtime_catalog_document() -> CatalogDocument {
    catalog_document(
        sandbox_operation_catalog::runtime::runtime_catalog(),
        sandbox_cli::projection::runtime::catalog_projection(),
    )
    .expect("runtime catalog")
}

fn observability_catalog_document() -> CatalogDocument {
    catalog_document(
        sandbox_operation_catalog::observability::observability_catalog(),
        sandbox_cli::projection::observability::catalog_projection(),
    )
    .expect("observability catalog")
}

fn operation<'a>(catalog: &'a CatalogDocument, name: &str) -> &'a OperationSpecDocument {
    catalog
        .semantic
        .operations
        .iter()
        .find(|spec| spec.name == name)
        .expect("operation")
}

fn scope_policy(
    catalog: &CatalogDocument,
    name: &str,
) -> sandbox_operation_contract::OperationScopePolicy {
    catalog
        .semantic
        .routes
        .iter()
        .find(|route| route.operation == name)
        .expect("route")
        .scope_policy
}

#[test]
fn argv_projection_and_shared_value_builder_match_manager_requests() {
    let catalog = manager_catalog_document();
    let argv_request = build_request_from_catalog_with_id(
        BuildRequestInput {
            execution_space: OperationDomain::Manager,
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
            spec: operation(&catalog, "create_sandbox"),
            scope_policy: scope_policy(&catalog, "create_sandbox"),
            scope_selector: None,
            arguments: json!({
                "image": "ubuntu:24.04",
                "workspace_root": "/workspace"
            }),
        },
        "request-1",
    )
    .expect("value request");

    assert_eq!(value_request, argv_request);
    assert_eq!(value_request.args["count"], 1);
    assert_eq!(value_request.scope, OperationScope::system());
}

#[test]
fn workspace_root_flag_remains_accepted() {
    let catalog = manager_catalog_document();
    let request = build_request_from_catalog_with_id(
        BuildRequestInput {
            execution_space: OperationDomain::Manager,
            operation: "create_sandbox".to_owned(),
            operation_argv: vec![
                "--image".to_owned(),
                "ubuntu:24.04".to_owned(),
                "--workspace-root".to_owned(),
                "/workspace".to_owned(),
            ],
            sandbox_id: None,
        },
        &catalog,
        "request-1",
    )
    .expect("--workspace-root remains accepted");

    assert_eq!(request.args["workspace_root"], "/workspace");
}

#[test]
fn runtime_selector_is_required_and_removed_from_operation_args() {
    let catalog = runtime_catalog_document();
    let request = build_request_from_catalog_with_id(
        BuildRequestInput {
            execution_space: OperationDomain::Runtime,
            operation: "read_command_lines".to_owned(),
            operation_argv: vec!["--command-session-id".to_owned(), "cmd-1".to_owned()],
            sandbox_id: Some("eos-runtime".to_owned()),
        },
        &catalog,
        "request-2",
    )
    .expect("runtime request");

    assert_eq!(request.op, "read_command_lines");
    assert_eq!(request.scope, OperationScope::sandbox("eos-runtime"));
    assert_eq!(
        request.args,
        json!({"command_session_id": "cmd-1", "start_offset": 0, "limit": 200})
    );
}

#[test]
fn argv_projection_and_shared_value_builder_match_file_edits() {
    let catalog = runtime_catalog_document();
    let argv_request = build_request_from_catalog_with_id(
        BuildRequestInput {
            execution_space: OperationDomain::Runtime,
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
            spec: operation(&catalog, "file_edit"),
            scope_policy: scope_policy(&catalog, "file_edit"),
            scope_selector: Some("eos-runtime".to_owned()),
            arguments: json!({
                "sandbox_id": "eos-runtime",
                "path": "notes.txt",
                "edits": [{"old_string": "draft", "new_string": "final"}]
            }),
        },
        "request-edit",
    )
    .expect("value request");

    assert_eq!(value_request, argv_request);
    assert!(value_request.args["edits"].is_array());
}

#[test]
fn file_edit_keeps_cli_labels_while_sharing_value_validation() {
    let catalog = runtime_catalog_document();
    let argv_error = build_request_from_catalog_with_id(
        BuildRequestInput {
            execution_space: OperationDomain::Runtime,
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
            spec: operation(&catalog, "file_edit"),
            scope_policy: scope_policy(&catalog, "file_edit"),
            scope_selector: Some("eos-runtime".to_owned()),
            arguments: json!({
                "path": "notes.txt",
                "edits": "[]"
            }),
        },
        "request-edit-value-error",
    )
    .expect_err("string edits rejected");

    assert_eq!(argv_error.message(), "--edits must be a JSON array");
    assert_eq!(value_error.message(), "edits must be a JSON array");
}

#[test]
fn observability_adapter_applies_the_catalog_migration_resolver() {
    let catalog = observability_catalog_document();
    let aggregate = build_request_from_catalog_with_id(
        BuildRequestInput {
            execution_space: OperationDomain::Observability,
            operation: "snapshot".to_owned(),
            operation_argv: Vec::new(),
            sandbox_id: None,
        },
        &catalog,
        "request-3",
    )
    .expect("aggregate request");
    let scoped = build_request_from_catalog_with_id(
        BuildRequestInput {
            execution_space: OperationDomain::Observability,
            operation: "trace".to_owned(),
            operation_argv: vec!["--sandbox-id".to_owned(), "eos-observe".to_owned()],
            sandbox_id: None,
        },
        &catalog,
        "request-4",
    )
    .expect("scoped request");

    assert_eq!(aggregate.op, "snapshot");
    assert_eq!(aggregate.scope, OperationScope::system());
    assert_eq!(aggregate.args, json!({}));
    assert_eq!(scoped.op, "get_observability");
    assert_eq!(scoped.scope, OperationScope::sandbox("eos-observe"));
    assert_eq!(scoped.args, json!({"view": "trace", "trace_id": "last"}));
}
