//! Live `get_observability` view router. Serves runtime-derived and live-state
//! views without reading the NDJSON log; the SQLite snapshot path stays on its
//! own private op.

use sandbox_observability::{sample_layerstack, ObservabilitySnapshotReadOptions};
use sandbox_protocol::{error_kind, Request, Response};
use sandbox_runtime::SandboxRuntimeOperations;
use serde_json::{json, Value};

use super::layerstack::{layerstack_view_value, stack_summary_value, workspace_layerstack_value};
use super::DaemonObservability;

const MAX_RESOURCE_WINDOW_MS: u64 = 600_000;

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
        let since = now_unix_ms().saturating_sub(i64::try_from(window_ms).unwrap_or(i64::MAX));
        object.insert(
            "trend".to_owned(),
            Value::Array(observability.stack_trend(since)),
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
    let upper_bytes = observability
        .and_then(|observability| {
            observability
                .read_snapshot_value(&ObservabilitySnapshotReadOptions {
                    resource_window_ms: None,
                })
                .ok()
        })
        .and_then(|live| workspace_upper_bytes(&live, workspace));
    match workspace_layerstack_value(&snapshot.workspaces, workspace, upper_bytes) {
        Some(value) => Response::ok(value),
        None => Response::fault(
            error_kind::INVALID_REQUEST,
            format!("unknown workspace: {workspace}"),
        ),
    }
}

fn workspace_upper_bytes(live: &Value, workspace: &str) -> Option<u64> {
    live.get("workspaces")?
        .as_array()?
        .iter()
        .find(|entry| entry.get("workspace_id").and_then(Value::as_str) == Some(workspace))?
        .get("resources")?
        .get("latest")?
        .get("disk")?
        .get("upperdir_bytes")?
        .as_u64()
}

fn snapshot_view_response(
    operations: &SandboxRuntimeOperations,
    observability: Option<&DaemonObservability>,
    request: &Request,
) -> Response {
    let Some(observability) = observability else {
        return observability_unconfigured();
    };
    let window_ms = match resource_window_ms(request) {
        Ok(window_ms) => window_ms,
        Err(response) => return response,
    };
    let mut snapshot = match observability.read_snapshot_value(&ObservabilitySnapshotReadOptions {
        resource_window_ms: window_ms,
    }) {
        Ok(value) => value,
        Err(response) => return response,
    };
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
    let Some(observability) = observability else {
        return observability_unconfigured();
    };
    let scope = match request.optional_string("scope") {
        Ok(scope) => scope.unwrap_or_else(|| "sandbox".to_owned()),
        Err(response) => return response,
    };
    let window_ms = match resource_window_ms(request) {
        Ok(window_ms) => window_ms,
        Err(response) => return response,
    };
    let snapshot = match observability.read_snapshot_value(&ObservabilitySnapshotReadOptions {
        resource_window_ms: window_ms,
    }) {
        Ok(value) => value,
        Err(response) => return response,
    };
    Response::ok(json!({
        "view": "cgroup",
        "scope": scope,
        "series": resource_series_for_scope(&snapshot, &scope),
    }))
}

/// Pick the resource bundle (`{latest, history}`) for one scope out of a live
/// snapshot value: the sandbox root, or a workspace by id.
pub(crate) fn resource_series_for_scope(snapshot: &Value, scope: &str) -> Value {
    if scope == "sandbox" {
        return snapshot.get("resources").cloned().unwrap_or(Value::Null);
    }
    snapshot
        .get("workspaces")
        .and_then(Value::as_array)
        .and_then(|workspaces| {
            workspaces.iter().find(|workspace| {
                workspace.get("workspace_id").and_then(Value::as_str) == Some(scope)
            })
        })
        .and_then(|workspace| workspace.get("resources").cloned())
        .unwrap_or(Value::Null)
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

fn now_unix_ms() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};

    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| i64::try_from(elapsed.as_millis()).unwrap_or(i64::MAX))
        .unwrap_or_default()
}
