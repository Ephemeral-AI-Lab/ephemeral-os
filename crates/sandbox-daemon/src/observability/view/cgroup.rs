use sandbox_protocol::{Request, Response};
use serde_json::json;

use crate::observability::{DaemonObservability, MAX_RESOURCE_WINDOW_MS};

pub(super) fn cgroup_view_response(
    observability: Option<&DaemonObservability>,
    request: &Request,
) -> Response {
    let scope = match request.optional_string("scope") {
        Ok(scope) => scope.unwrap_or_else(|| "sandbox".to_owned()),
        Err(response) => return response,
    };
    let window_ms = match super::resource_window_ms(request) {
        Ok(window_ms) => window_ms.unwrap_or(MAX_RESOURCE_WINDOW_MS),
        Err(response) => return response,
    };
    let Some(observability) = observability else {
        return super::observability_unconfigured();
    };
    Response::ok(json!({
        "view": "cgroup",
        "scope": scope,
        "series": observability.cgroup_series(&scope, window_ms),
    }))
}
