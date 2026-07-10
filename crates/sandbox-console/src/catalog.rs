//! `/api/catalog`: the management, runtime, and observability semantic
//! operation catalogs, rendered once from their shared declarations.

use std::sync::OnceLock;

use http::StatusCode;
use hyper::Response;
use sandbox_operation_contract::catalog_to_value;
use serde_json::{json, Value};

use crate::response::{self, BoxBody};

static CATALOGS: OnceLock<Value> = OnceLock::new();

pub fn handle() -> Response<BoxBody> {
    response::json_value(StatusCode::OK, CATALOGS.get_or_init(catalogs))
}

fn catalogs() -> Value {
    json!({
        "management": catalog_to_value(sandbox_operation_catalog::manager::manager_catalog()),
        "runtime": catalog_to_value(sandbox_operation_catalog::runtime::runtime_catalog()),
        "observability": catalog_to_value(
            sandbox_operation_catalog::observability::observability_catalog()
        ),
    })
}
