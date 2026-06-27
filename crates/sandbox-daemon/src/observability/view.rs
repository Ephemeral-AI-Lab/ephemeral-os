//! Live `get_observability` view router. Serves every view from live runtime
//! state plus the leaf `Reader` over the one NDJSON log — no storage engine.

use sandbox_observability::sample_layerstack;
use sandbox_protocol::{error_kind, Request, Response};
use sandbox_runtime::SandboxRuntimeOperations;
use serde_json::{json, Value};

use super::layerstack::{layerstack_view_value, stack_summary_value, workspace_layerstack_value};
use super::{DaemonObservability, MAX_RESOURCE_WINDOW_MS};

pub(crate) fn observability_view_response(
    operations: &SandboxRuntimeOperations,
    observability: Option<&DaemonObservability>,
    request: &Request,
) -> Response {
    let view = match request.optional_string("view") {
        Ok(view) => view,
        Err(response) => return response,
    };
    match view.as_deref() {
        Some("layerstack") => layerstack_view_response(operations, observability, request),
        Some("snapshot") => snapshot_view_response(operations, observability, request),
        Some("cgroup") => cgroup_view_response(observability, request),
        Some(other) => Response::fault(
            error_kind::INVALID_REQUEST,
            format!("unsupported observability view: {other}"),
        ),
        None => Response::fault(
            error_kind::INVALID_REQUEST,
            "observability request requires a view".to_owned(),
        ),
    }
}

fn layerstack_view_response(
    operations: &SandboxRuntimeOperations,
    observability: Option<&DaemonObservability>,
    request: &Request,
) -> Response {
    let workspace = match request.optional_string("workspace") {
        Ok(workspace) => workspace.filter(|workspace| !workspace.trim().is_empty()),
        Err(response) => return response,
    };
    if let Some(workspace) = workspace {
        return workspace_view_response(operations, observability, workspace.trim());
    }
    let observation = match operations.observe_layerstack() {
        Ok(observation) => observation,
        Err(error) => {
            return Response::fault(
                error_kind::INTERNAL_ERROR,
                format!("layerstack observe failed: {error}"),
            )
        }
    };
    let bytes = sample_layerstack(operations.layer_stack_root());
    let mut view = layerstack_view_value(&observation, &bytes);
    let window_ms = match resource_window_ms(request) {
        Ok(window_ms) => window_ms,
        Err(response) => return response,
    };
    if let (Some(observability), Some(window_ms), Value::Object(object)) =
        (observability, window_ms, &mut view)
    {
        object.insert(
            "trend".to_owned(),
            Value::Array(observability.stack_trend(window_ms)),
        );
    }
    Response::ok(view)
}

fn workspace_view_response(
    operations: &SandboxRuntimeOperations,
    observability: Option<&DaemonObservability>,
    workspace: &str,
) -> Response {
    let snapshot = operations.observability_snapshot();
    let upper_bytes =
        observability.and_then(|observability| observability.latest_upper_bytes(workspace));
    match workspace_layerstack_value(&snapshot.workspaces, workspace, upper_bytes) {
        Some(value) => Response::ok(value),
        None => Response::fault(
            error_kind::INVALID_REQUEST,
            format!("unknown workspace: {workspace}"),
        ),
    }
}

/// The live `snapshot` view. Also serves the legacy `get_observability_snapshot`
/// op as a thin no-SQLite alias (the request carries no view-specific args).
pub(crate) fn snapshot_view_response(
    operations: &SandboxRuntimeOperations,
    observability: Option<&DaemonObservability>,
    _request: &Request,
) -> Response {
    let Some(observability) = observability else {
        return observability_unconfigured();
    };
    let mut snapshot = observability.snapshot_value(operations.observability_snapshot());
    if let (Ok(observation), Value::Object(object)) =
        (operations.observe_layerstack(), &mut snapshot)
    {
        let bytes = sample_layerstack(operations.layer_stack_root());
        object.insert(
            "stack".to_owned(),
            stack_summary_value(&observation, &bytes),
        );
    }
    Response::ok(snapshot)
}

fn cgroup_view_response(
    observability: Option<&DaemonObservability>,
    request: &Request,
) -> Response {
    let scope = match request.optional_string("scope") {
        Ok(scope) => scope.unwrap_or_else(|| "sandbox".to_owned()),
        Err(response) => return response,
    };
    let window_ms = match resource_window_ms(request) {
        Ok(window_ms) => window_ms.unwrap_or(MAX_RESOURCE_WINDOW_MS),
        Err(response) => return response,
    };
    let Some(observability) = observability else {
        return observability_unconfigured();
    };
    Response::ok(json!({
        "view": "cgroup",
        "scope": scope,
        "series": observability.cgroup_series(&scope, window_ms),
    }))
}

fn resource_window_ms(request: &Request) -> Result<Option<u64>, Response> {
    let window_ms = request.optional_u64("window_ms")?;
    if let Some(window_ms) = window_ms {
        if window_ms > MAX_RESOURCE_WINDOW_MS {
            return Err(Response::fault(
                error_kind::INVALID_REQUEST,
                format!("window_ms exceeds max ({MAX_RESOURCE_WINDOW_MS})"),
            ));
        }
    }
    Ok(window_ms)
}

fn observability_unconfigured() -> Response {
    Response::fault(
        error_kind::INTERNAL_ERROR,
        "daemon observability is not configured".to_owned(),
    )
}
