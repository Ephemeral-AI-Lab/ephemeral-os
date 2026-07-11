use sandbox_observability_telemetry::RawFilter;
use sandbox_operation_contract::{error, OperationRequest, OperationResponse};
use sandbox_runtime_layerstack::LayerRef;
use serde_json::{json, Value};

use crate::ports::{ObservabilityInput, QueryLimits};
use crate::response;

pub(crate) fn snapshot(
    input: &dyn ObservabilityInput,
    _request: &OperationRequest,
) -> OperationResponse {
    let Some(context) = input.query_context() else {
        return observability_unconfigured();
    };
    let mut value = response::snapshot_value(&context, input.observability_snapshot());
    if let (Ok(observation), Value::Object(object)) = (input.observe_layerstack(), &mut value) {
        object.insert(
            "stack".to_owned(),
            response::stack_summary_value(&observation, &input.layerstack_bytes()),
        );
    }
    OperationResponse::ok(value)
}

pub(crate) fn cgroup(
    input: &dyn ObservabilityInput,
    request: &OperationRequest,
) -> OperationResponse {
    let scope = match request.optional_string("scope") {
        Ok(scope) => scope.unwrap_or_else(|| "sandbox".to_owned()),
        Err(response) => return response,
    };
    let Some(context) = input.query_context() else {
        return observability_unconfigured();
    };
    let limits = input.query_limits();
    let window_ms = match resource_window_ms(request, limits.resource_window_ms) {
        Ok(window_ms) => window_ms.unwrap_or(limits.resource_window_ms),
        Err(response) => return response,
    };
    OperationResponse::ok(json!({
        "view": "cgroup",
        "scope": scope,
        "series": response::cgroup_series(&context.reader, &scope, window_ms),
    }))
}

pub(crate) fn events(
    input: &dyn ObservabilityInput,
    request: &OperationRequest,
) -> OperationResponse {
    let Some(context) = input.query_context() else {
        return observability_unconfigured();
    };
    let filter = match event_filter(request) {
        Ok(filter) => filter,
        Err(response) => return response,
    };
    let last_n = match request.optional_u64("last_n") {
        Ok(last_n) => last_n,
        Err(response) => return response,
    };
    let mut events = context.reader.events(filter);
    if let Some(last_n) = last_n {
        let keep = usize::try_from(last_n)
            .unwrap_or(usize::MAX)
            .min(events.len());
        events.drain(..events.len() - keep);
    }
    let events = serde_json::to_value(events).unwrap_or_else(|_| Value::Array(Vec::new()));
    OperationResponse::ok(json!({ "view": "events", "events": events }))
}

pub(crate) fn trace(
    input: &dyn ObservabilityInput,
    request: &OperationRequest,
) -> OperationResponse {
    let Some(context) = input.query_context() else {
        return observability_unconfigured();
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
        context.reader.latest_root_trace().unwrap_or(id)
    } else {
        id
    };
    let spans = serde_json::to_value(context.reader.trace(&id))
        .unwrap_or_else(|_| Value::Array(Vec::new()));
    OperationResponse::ok(json!({ "view": "trace", "trace": id, "spans": spans }))
}

pub(crate) fn layerstack(
    input: &dyn ObservabilityInput,
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
    let limits = input.query_limits();
    if let Some(layer) = layer {
        return layer_response(input, request, layer.trim(), limits);
    }
    if let Some(workspace) = workspace {
        return workspace_response(input, workspace.trim());
    }
    let observation = match input.observe_layerstack() {
        Ok(observation) => observation,
        Err(error) => {
            return OperationResponse::fault(
                error::INTERNAL_ERROR,
                format!("layerstack observe failed: {error}"),
            )
        }
    };
    let mut value = response::layerstack_value(&observation, &input.layerstack_bytes());
    let window_ms = match resource_window_ms(request, limits.resource_window_ms) {
        Ok(window_ms) => window_ms,
        Err(response) => return response,
    };
    if let (Some(context), Some(window_ms), Value::Object(object)) =
        (input.query_context(), window_ms, &mut value)
    {
        object.insert(
            "trend".to_owned(),
            Value::Array(response::stack_trend(&context.reader, window_ms)),
        );
    }
    OperationResponse::ok(value)
}

fn layer_response(
    input: &dyn ObservabilityInput,
    request: &OperationRequest,
    layer_id: &str,
    limits: QueryLimits,
) -> OperationResponse {
    let limit = match layer_delta_limit(request, limits) {
        Ok(limit) => limit,
        Err(response) => return response,
    };
    let observation = match input.observe_layerstack() {
        Ok(observation) => observation,
        Err(error) => {
            return OperationResponse::fault(
                error::INTERNAL_ERROR,
                format!("layerstack observe failed: {error}"),
            )
        }
    };
    let Some(layer) = find_layer(&observation, layer_id) else {
        return OperationResponse::fault(
            error::INVALID_REQUEST,
            format!("unknown layer: {layer_id}"),
        );
    };
    match input.describe_layer_delta(&layer.path, limit) {
        Ok(delta) => OperationResponse::ok(response::layer_delta_value(&layer.layer_id, &delta)),
        Err(error) => OperationResponse::fault(
            error::INTERNAL_ERROR,
            format!("layer delta inspect failed: {error}"),
        ),
    }
}

fn workspace_response(input: &dyn ObservabilityInput, workspace: &str) -> OperationResponse {
    let snapshot = input.observability_snapshot();
    let upper_bytes = input
        .query_context()
        .and_then(|context| response::latest_upper_bytes(&context.reader, workspace));
    match response::workspace_layerstack_value(&snapshot.workspaces, workspace, upper_bytes) {
        Some(value) => OperationResponse::ok(value),
        None => OperationResponse::fault(
            error::INVALID_REQUEST,
            format!("unknown workspace: {workspace}"),
        ),
    }
}

fn find_layer<'a>(
    observation: &'a sandbox_runtime_layerstack::service::StackObservation,
    layer_id: &str,
) -> Option<&'a LayerRef> {
    observation
        .layers
        .iter()
        .map(|status| &status.layer)
        .find(|layer| layer.layer_id == layer_id)
}

fn event_filter(request: &OperationRequest) -> Result<RawFilter, OperationResponse> {
    Ok(RawFilter {
        name: optional_filter(request, "name")?,
        since_ms: since_ms(request)?,
        ..RawFilter::default()
    })
}

fn optional_filter(
    request: &OperationRequest,
    field: &str,
) -> Result<Option<String>, OperationResponse> {
    Ok(request
        .optional_string(field)?
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty()))
}

fn since_ms(request: &OperationRequest) -> Result<i64, OperationResponse> {
    Ok(request
        .optional_u64("since_ms")?
        .map(|value| i64::try_from(value).unwrap_or(i64::MAX))
        .unwrap_or(0))
}

fn resource_window_ms(
    request: &OperationRequest,
    max_window_ms: u64,
) -> Result<Option<u64>, OperationResponse> {
    let window_ms = request.optional_u64("window_ms")?;
    if let Some(window_ms) = window_ms {
        if window_ms > max_window_ms {
            return Err(OperationResponse::fault(
                error::INVALID_REQUEST,
                format!("window_ms exceeds max ({max_window_ms})"),
            ));
        }
    }
    Ok(window_ms)
}

fn layer_delta_limit(
    request: &OperationRequest,
    limits: QueryLimits,
) -> Result<usize, OperationResponse> {
    let limit = request
        .optional_usize("limit")?
        .unwrap_or(limits.layer_delta_default_limit);
    if limit > limits.layer_delta_max_limit {
        return Err(OperationResponse::fault(
            error::INVALID_REQUEST,
            format!("limit exceeds max ({})", limits.layer_delta_max_limit),
        ));
    }
    Ok(limit)
}

fn observability_unconfigured() -> OperationResponse {
    OperationResponse::fault(
        error::INTERNAL_ERROR,
        "daemon observability is not configured".to_owned(),
    )
}
