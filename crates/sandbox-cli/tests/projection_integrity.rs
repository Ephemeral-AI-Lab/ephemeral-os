#![cfg(all(feature = "manager", feature = "runtime", feature = "observability"))]

use std::collections::HashSet;

use sandbox_cli::projection::document::{catalog_document, OperationProjectionDocument};
use sandbox_cli::projection::CatalogProjection;
use sandbox_operation_catalog::{manager, observability, runtime};
use sandbox_operation_contract::{OperationCatalog, OperationDomain, OperationVisibility};

#[test]
fn cli_projection_is_bidirectional_with_public_routes() {
    assert_projection_integrity(
        manager::manager_catalog(),
        sandbox_cli::projection::manager::catalog_projection(),
    );
    assert_projection_integrity(
        runtime::runtime_catalog(),
        sandbox_cli::projection::runtime::catalog_projection(),
    );
    assert_projection_integrity(
        observability::observability_catalog(),
        sandbox_cli::projection::observability::catalog_projection(),
    );
}

#[test]
fn operations_without_overrides_get_derived_projections() {
    // An empty override table exercises the derived fallback for every
    // public operation: kebab-case flags, no positionals, usage from the spec.
    let document = catalog_document(
        runtime::runtime_catalog(),
        CatalogProjection {
            operation_execution_space: OperationDomain::Runtime,
            operations: &[],
        },
    )
    .expect("derived-only projection");

    assert_eq!(
        document.projection.len(),
        document.semantic.operations.len()
    );

    let exec = document
        .projection
        .iter()
        .find(|operation| operation.name == "exec_command")
        .expect("derived exec_command projection");
    assert_eq!(exec.path, ["runtime", "exec_command"]);
    assert!(exec.examples.is_empty());
    assert_eq!(
        exec.arguments
            .iter()
            .map(|argument| argument.flag.as_deref())
            .collect::<Vec<_>>(),
        [
            Some("--workspace-session-id"),
            Some("--cmd"),
            Some("--timeout-ms"),
            Some("--yield-time-ms"),
        ]
    );
    assert!(exec
        .arguments
        .iter()
        .all(|argument| argument.positional.is_none()));
    assert_eq!(
        exec.usage,
        "sandbox-runtime-cli --sandbox-id ID exec_command [--workspace-session-id WORKSPACE_SESSION_ID] \
         --cmd CMD [--timeout-ms TIMEOUT_MS] [--yield-time-ms YIELD_TIME_MS]"
    );
}

#[test]
fn overrides_must_reference_public_operations() {
    let error = catalog_document(
        runtime::runtime_catalog(),
        CatalogProjection {
            operation_execution_space: OperationDomain::Runtime,
            operations: &[sandbox_cli::projection::OperationProjection {
                name: "not_an_operation",
                path: &["runtime", "not_an_operation"],
                usage: "sandbox-runtime-cli --sandbox-id ID not_an_operation",
                examples: &[],
                arguments: &[],
            }],
        },
    )
    .expect_err("unknown override must be rejected");
    assert!(error
        .message()
        .contains("projected operation is absent from semantic catalog"));
}

fn assert_projection_integrity(catalog: OperationCatalog, overrides: CatalogProjection) {
    let document = catalog_document(catalog, overrides).expect("valid CLI projection");
    let routed_operations = document
        .semantic
        .routes
        .iter()
        .map(|route| route.operation.as_str())
        .collect::<HashSet<_>>();

    for projected in &document.projection {
        assert!(document.semantic.routes.iter().any(|route| {
            route.operation == projected.name && route.visibility == OperationVisibility::Public
        }));
        assert_unique_argument_bindings(projected);
    }

    for routed in routed_operations {
        assert_eq!(
            document
                .projection
                .iter()
                .filter(|projected| projected.name == routed)
                .count(),
            1,
            "public operation must have exactly one CLI projection: {routed}"
        );
    }

    for internal in [
        "create_workspace_session",
        "destroy_workspace_session",
        "squash_layerstack",
        "export_layerstack",
        "read_export_chunk",
        "file_list",
    ] {
        assert!(document
            .projection
            .iter()
            .all(|projected| projected.name != internal));
    }
}

fn assert_unique_argument_bindings(operation: &OperationProjectionDocument) {
    let mut flags = HashSet::new();
    let mut positionals = HashSet::new();

    for argument in &operation.arguments {
        if let Some(flag) = argument.flag.as_deref() {
            assert!(
                flags.insert(flag),
                "duplicate flag in {}: {flag}",
                operation.name
            );
        }
        for flag in &argument.additional_flags {
            assert!(
                flags.insert(flag.as_str()),
                "duplicate flag in {}: {flag}",
                operation.name
            );
        }
        if let Some(positional) = argument.positional.as_deref() {
            assert!(
                positionals.insert(positional),
                "duplicate positional in {}: {positional}",
                operation.name
            );
        }
    }
}
