use sandbox_observability::sample_layerstack;
use sandbox_protocol::{error_kind, Request, Response};
use sandbox_runtime::SandboxRuntimeOperations;
use serde_json::Value;

use crate::observability::layerstack::{
    layer_delta_value, layerstack_view_value, workspace_layerstack_value,
};
use crate::observability::DaemonObservability;

const DEFAULT_LAYER_DELTA_LIMIT: usize = 500;
const MAX_LAYER_DELTA_LIMIT: usize = 5_000;

pub(super) fn layerstack_view_response(
    operations: &SandboxRuntimeOperations,
    observability: Option<&DaemonObservability>,
    request: &Request,
) -> Response {
    let workspace = match request.optional_string("workspace_id") {
        Ok(workspace) => workspace.filter(|workspace| !workspace.trim().is_empty()),
        Err(response) => return response,
    };
    let layer = match request.optional_string("layer_id") {
        Ok(layer) => layer.filter(|layer| !layer.trim().is_empty()),
        Err(response) => return response,
    };
    if workspace.is_some() && layer.is_some() {
        return Response::fault(
            error_kind::INVALID_REQUEST,
            "layerstack request cannot include both workspace_id and layer_id".to_owned(),
        );
    }
    if let Some(layer) = layer {
        return layer_view_response(operations, request, layer.trim());
    }
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
    let window_ms = match super::resource_window_ms(request) {
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

fn layer_view_response(
    operations: &SandboxRuntimeOperations,
    request: &Request,
    layer_id: &str,
) -> Response {
    let limit = match layer_delta_limit(request) {
        Ok(limit) => limit,
        Err(response) => return response,
    };
    let observation = match operations.observe_layerstack() {
        Ok(observation) => observation,
        Err(error) => {
            return Response::fault(
                error_kind::INTERNAL_ERROR,
                format!("layerstack observe failed: {error}"),
            )
        }
    };
    let Some(layer) = observation
        .layers
        .iter()
        .map(|status| &status.layer)
        .find(|layer| layer.layer_id == layer_id)
    else {
        return Response::fault(
            error_kind::INVALID_REQUEST,
            format!("unknown layer: {layer_id}"),
        );
    };
    let layer_dir = operations.layer_stack_root().join(&layer.path);
    let delta = match sandbox_runtime::describe_layer_delta(&layer_dir, limit) {
        Ok(delta) => delta,
        Err(error) => {
            return Response::fault(
                error_kind::INTERNAL_ERROR,
                format!("layer delta inspect failed: {error}"),
            )
        }
    };
    Response::ok(layer_delta_value(&layer.layer_id, &delta))
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

fn layer_delta_limit(request: &Request) -> Result<usize, Response> {
    let limit = request
        .optional_usize("limit")?
        .unwrap_or(DEFAULT_LAYER_DELTA_LIMIT);
    if limit > MAX_LAYER_DELTA_LIMIT {
        return Err(Response::fault(
            error_kind::INVALID_REQUEST,
            format!("limit exceeds max ({MAX_LAYER_DELTA_LIMIT})"),
        ));
    }
    Ok(limit)
}
