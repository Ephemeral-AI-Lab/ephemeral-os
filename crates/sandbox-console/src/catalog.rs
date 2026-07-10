//! `/api/catalog`: the management, runtime, and observability operation
//! catalogs, rendered once from the same spec-only crates the CLIs link so
//! browser forms cannot drift from the protocol.

use std::sync::OnceLock;

use http::StatusCode;
use hyper::Response;
use sandbox_protocol::catalog_to_value;
use serde_json::{json, Value};

use crate::response::{self, BoxBody};

static CATALOGS: OnceLock<Value> = OnceLock::new();

pub fn handle() -> Response<BoxBody> {
    let catalogs = CATALOGS.get_or_init(|| {
        json!({
            "management": catalog_to_value(sandbox_manager_operations::manager_catalog()),
            "runtime": catalog_to_value(sandbox_runtime_operations::runtime_catalog()),
            "observability": catalog_to_value(
                sandbox_observability_operations::observability_catalog()
            ),
        })
    });
    response::json_value(StatusCode::OK, catalogs)
}
