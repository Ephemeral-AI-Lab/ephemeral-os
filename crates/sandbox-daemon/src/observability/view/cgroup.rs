use sandbox_operation_contract::{OperationRequest, OperationResponse};
use serde_json::json;

use crate::observability::DaemonObservability;

pub(super) fn cgroup_view_response(
    observability: Option<&DaemonObservability>,
    request: &OperationRequest,
) -> OperationResponse {
    let scope = match request.optional_string("scope") {
        Ok(scope) => scope.unwrap_or_else(|| "sandbox".to_owned()),
        Err(response) => return response,
    };
    let Some(observability) = observability else {
        return super::observability_unconfigured();
    };
    let max_window_ms = observability.views.resource_window_ms;
    let window_ms = match super::resource_window_ms(request, max_window_ms) {
        Ok(window_ms) => window_ms.unwrap_or(max_window_ms),
        Err(response) => return response,
    };
    OperationResponse::ok(json!({
        "view": "cgroup",
        "scope": scope,
        "series": observability.cgroup_series(&scope, window_ms),
    }))
}
