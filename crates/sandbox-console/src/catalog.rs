//! `/api/catalog`: the management, runtime, and observability operation
//! catalogs, rendered once from the semantic catalogs and CLI-owned
//! compatibility projection.

use std::sync::OnceLock;

use http::StatusCode;
use hyper::Response;
use sandbox_cli::projection::document::{catalog_document, catalog_to_value};
use serde_json::{json, Value};

use crate::response::{self, BoxBody};

static CATALOGS: OnceLock<Result<Value, String>> = OnceLock::new();

pub fn handle() -> Response<BoxBody> {
    match CATALOGS.get_or_init(catalogs) {
        Ok(catalogs) => response::json_value(StatusCode::OK, catalogs),
        Err(message) => response::json_value(
            StatusCode::INTERNAL_SERVER_ERROR,
            &sandbox_operation_contract::error_response_with_details(
                "internal_error",
                message,
                json!({}),
            ),
        ),
    }
}

fn catalogs() -> Result<Value, String> {
    let management = catalog_document(
        sandbox_operation_catalog::manager::manager_catalog(),
        sandbox_cli::projection::manager::catalog_projection(),
    )
    .map_err(|error| error.to_string())?;
    let runtime = catalog_document(
        sandbox_operation_catalog::runtime::runtime_catalog(),
        sandbox_cli::projection::runtime::catalog_projection(),
    )
    .map_err(|error| error.to_string())?;
    let observability = catalog_document(
        sandbox_operation_catalog::observability::observability_catalog(),
        sandbox_cli::projection::observability::catalog_projection(),
    )
    .map_err(|error| error.to_string())?;

    Ok(json!({
        "management": catalog_to_value(&management),
        "runtime": catalog_to_value(&runtime),
        "observability": catalog_to_value(&observability),
    }))
}
