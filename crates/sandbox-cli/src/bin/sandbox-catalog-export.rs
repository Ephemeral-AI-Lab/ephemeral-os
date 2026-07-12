//! Deterministic, offline product catalog export for the external E2E suite.
//!
//! The binary only serializes compile-time operation declarations.  It never
//! reads a gateway configuration or opens a runtime connection.

use std::process::ExitCode;

use sandbox_operation_contract::catalog_to_value;
use serde_json::json;

fn main() -> ExitCode {
    let value = json!({
        "schema_version": 1,
        "kind": "ephemeral_sandbox_product_catalog",
        "domains": {
            "manager": catalog_to_value(sandbox_operation_catalog::manager::manager_catalog()),
            "runtime": catalog_to_value(sandbox_operation_catalog::runtime::runtime_catalog()),
            "observability": catalog_to_value(
                sandbox_operation_catalog::observability::observability_catalog(),
            ),
        },
    });
    match serde_json::to_writer_pretty(std::io::stdout().lock(), &value) {
        Ok(()) => {
            println!();
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("cannot write offline catalog: {error}");
            ExitCode::FAILURE
        }
    }
}
