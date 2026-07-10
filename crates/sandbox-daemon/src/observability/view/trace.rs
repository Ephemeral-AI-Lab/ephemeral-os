use sandbox_operation_contract::{error, OperationRequest, OperationResponse};
use serde_json::{json, Value};

use crate::observability::DaemonObservability;

pub(super) fn trace_view_response(
    observability: Option<&DaemonObservability>,
    request: &OperationRequest,
) -> OperationResponse {
    let Some(observability) = observability else {
        return super::observability_unconfigured();
    };
    let id = match request.optional_string("trace_id") {
        Ok(id) => id
            .map(|id| id.trim().to_owned())
            .filter(|id| !id.is_empty()),
        Err(response) => return response,
    };
    let Some(id) = id else {
        return OperationResponse::fault(
            error::INVALID_REQUEST,
            "trace view requires a trace id (--trace-id)".to_owned(),
        );
    };
    let id = if id == "last" {
        observability.latest_root_trace().unwrap_or(id)
    } else {
        id
    };
    let spans =
        serde_json::to_value(observability.trace(&id)).unwrap_or_else(|_| Value::Array(Vec::new()));
    OperationResponse::ok(json!({ "view": "trace", "trace": id, "spans": spans }))
}
