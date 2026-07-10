#![cfg(feature = "runtime")]

use sandbox_cli::help::{render_catalog_help, render_operation_help};
use sandbox_cli::projection::document::catalog_document;

fn runtime_catalog() -> sandbox_cli::projection::document::CatalogDocument {
    catalog_document(
        sandbox_runtime_operations::runtime_catalog(),
        sandbox_cli::projection::runtime::catalog_projection(),
    )
    .expect("runtime catalog projection")
}

#[test]
fn catalog_help_preserves_family_and_operation_order() {
    let help = render_catalog_help(&runtime_catalog(), "sandbox-cli runtime");

    assert!(help.contains("Sandbox Runtime Help"));
    assert!(
        help.find("Command").expect("command family") < help.find("File").expect("file family")
    );
    assert!(
        help.find("exec_command").expect("exec operation")
            < help
                .find("read_command_lines")
                .expect("read command lines operation")
    );
    assert!(help.contains("sandbox-cli runtime OPERATION"));
}

#[test]
fn operation_help_joins_semantics_and_cli_projection() {
    let help = render_operation_help(&runtime_catalog(), "exec_command", "sandbox-cli runtime")
        .expect("operation renders");

    assert!(help.contains("Family\n  Command"));
    assert!(help.contains("Description\n  Start a shell command"));
    assert!(help.contains("Usage\n  sandbox-runtime-cli --sandbox-id ID exec_command"));
    assert!(help.contains("COMMAND string required"));
    assert!(help.contains("Examples\n  sandbox-runtime-cli --sandbox-id ID exec_command pwd"));
}

#[test]
fn unknown_operation_help_preserves_search_suggestions() {
    let error = render_operation_help(&runtime_catalog(), "exec", "sandbox-cli runtime")
        .expect_err("unknown operation rejected");

    assert_eq!(error.operation(), "exec");
    assert_eq!(error.suggestions()[0].name, "exec_command");
    assert!(error
        .to_string()
        .contains("unknown runtime operation for help: exec"));
}
