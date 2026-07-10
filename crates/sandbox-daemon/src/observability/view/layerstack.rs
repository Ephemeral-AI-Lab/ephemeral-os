use sandbox_config::configs::observability::ViewsConfig;
use sandbox_observability::sample_layerstack;
use sandbox_operation_contract::{error, OperationRequest, OperationResponse};
use sandbox_runtime::SandboxRuntimeOperations;
use serde_json::Value;

use crate::observability::layerstack::{
    layer_delta_value, layerstack_view_value, workspace_layerstack_value,
};
use crate::observability::DaemonObservability;

pub(super) fn layerstack_view_response(
    operations: &SandboxRuntimeOperations,
    observability: Option<&DaemonObservability>,
    request: &OperationRequest,
) -> OperationResponse {
    let workspace = match request.optional_string("workspace_id") {
        Ok(workspace) => workspace.filter(|workspace| !workspace.trim().is_empty()),
        Err(response) => return response,
    };
    let layer = match request.optional_string("layer_id") {
        Ok(layer) => layer.filter(|layer| !layer.trim().is_empty()),
        Err(response) => return response,
    };
    if workspace.is_some() && layer.is_some() {
        return OperationResponse::fault(
            error::INVALID_REQUEST,
            "layerstack request cannot include both workspace_id and layer_id".to_owned(),
        );
    }
    let views =
        observability.map_or_else(ViewsConfig::default, |observability| observability.views);
    if let Some(layer) = layer {
        return layer_view_response(operations, request, layer.trim(), views);
    }
    if let Some(workspace) = workspace {
        return workspace_view_response(operations, observability, workspace.trim());
    }
    let observation = match operations.observe_layerstack() {
        Ok(observation) => observation,
        Err(error) => {
            return OperationResponse::fault(
                error::INTERNAL_ERROR,
                format!("layerstack observe failed: {error}"),
            )
        }
    };
    let sampling =
        observability.map_or_else(Default::default, |observability| observability.sampling);
    let bytes = sample_layerstack(operations.layer_stack_root(), sampling);
    let mut view = layerstack_view_value(&observation, &bytes);
    let window_ms = match super::resource_window_ms(request, views.resource_window_ms) {
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
    OperationResponse::ok(view)
}

fn layer_view_response(
    operations: &SandboxRuntimeOperations,
    request: &OperationRequest,
    layer_id: &str,
    views: ViewsConfig,
) -> OperationResponse {
    let limit = match layer_delta_limit(request, views) {
        Ok(limit) => limit,
        Err(response) => return response,
    };
    let observation = match operations.observe_layerstack() {
        Ok(observation) => observation,
        Err(error) => {
            return OperationResponse::fault(
                error::INTERNAL_ERROR,
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
        return OperationResponse::fault(
            error::INVALID_REQUEST,
            format!("unknown layer: {layer_id}"),
        );
    };
    let layer_dir = operations.layer_stack_root().join(&layer.path);
    let delta = match sandbox_runtime::describe_layer_delta(&layer_dir, limit) {
        Ok(delta) => delta,
        Err(error) => {
            return OperationResponse::fault(
                error::INTERNAL_ERROR,
                format!("layer delta inspect failed: {error}"),
            )
        }
    };
    OperationResponse::ok(layer_delta_value(&layer.layer_id, &delta))
}

fn workspace_view_response(
    operations: &SandboxRuntimeOperations,
    observability: Option<&DaemonObservability>,
    workspace: &str,
) -> OperationResponse {
    let snapshot = operations.observability_snapshot();
    let upper_bytes =
        observability.and_then(|observability| observability.latest_upper_bytes(workspace));
    match workspace_layerstack_value(&snapshot.workspaces, workspace, upper_bytes) {
        Some(value) => OperationResponse::ok(value),
        None => OperationResponse::fault(
            error::INVALID_REQUEST,
            format!("unknown workspace: {workspace}"),
        ),
    }
}

fn layer_delta_limit(
    request: &OperationRequest,
    views: ViewsConfig,
) -> Result<usize, OperationResponse> {
    let limit = request
        .optional_usize("limit")?
        .unwrap_or(views.layer_delta_default_limit);
    if limit > views.layer_delta_max_limit {
        return Err(OperationResponse::fault(
            error::INVALID_REQUEST,
            format!("limit exceeds max ({})", views.layer_delta_max_limit),
        ));
    }
    Ok(limit)
}
