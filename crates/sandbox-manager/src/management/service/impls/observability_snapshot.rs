use std::sync::Arc;
use std::time::Duration;

use sandbox_operation_catalog::observability::SNAPSHOT_SPEC;
use sandbox_operation_contract::{OperationRequest, OperationScope};
use serde_json::{json, Map, Value};

use super::resource_metrics::latest_resource_value;
use crate::operations::{ManagerServices, ObservabilitySnapshotLimits};
use crate::{
    ManagerError, SandboxDaemonClient, SandboxDaemonEndpoint, SandboxId, SandboxRecord,
    SandboxState,
};

const MAX_NODE_ERROR_BYTES: usize = 4_096;

#[derive(Clone, Debug)]
pub(crate) struct SnapshotOptions {
    pub(crate) sandbox_id: Option<SandboxId>,
}

pub(crate) fn observability_snapshot(
    services: &ManagerServices,
    options: SnapshotOptions,
    request_id: &str,
) -> Result<Vec<Value>, ManagerError> {
    let records = selected_records(services, options.sandbox_id.as_ref())?;
    Ok(aggregate_records(
        records,
        Arc::clone(&services.daemon_client),
        request_id,
        services.snapshot_limits,
        services,
    ))
}

fn selected_records(
    services: &ManagerServices,
    sandbox_id: Option<&SandboxId>,
) -> Result<Vec<SandboxRecord>, ManagerError> {
    match sandbox_id {
        Some(sandbox_id) => services
            .store
            .inspect(sandbox_id)
            .map(|record| vec![record]),
        None => Ok(services
            .store
            .list()?
            .into_iter()
            .filter(|record| record.state == SandboxState::Ready && record.daemon.is_some())
            .collect()),
    }
}

fn aggregate_records(
    records: Vec<SandboxRecord>,
    daemon_client: Arc<dyn SandboxDaemonClient>,
    request_id: &str,
    limits: ObservabilitySnapshotLimits,
    services: &ManagerServices,
) -> Vec<Value> {
    let mut nodes = Vec::with_capacity(records.len());
    for chunk in records.chunks(limits.max_concurrent_requests) {
        std::thread::scope(|scope| {
            let handles = chunk
                .iter()
                .cloned()
                .map(|record| {
                    let panic_record = record.clone();
                    let worker_client = Arc::clone(&daemon_client);
                    let worker_request_id = request_id.to_owned();
                    let handle = scope.spawn(move || {
                        sandbox_node(
                            record,
                            worker_client,
                            &worker_request_id,
                            limits.timeout_ms,
                            services,
                        )
                    });
                    (panic_record, handle)
                })
                .collect::<Vec<_>>();
            for (record, handle) in handles {
                match handle.join() {
                    Ok(node) => nodes.push(node),
                    Err(_) => nodes.push(unavailable_node(
                        &record,
                        record.daemon.as_ref(),
                        "manager observability aggregation worker panicked",
                    )),
                }
            }
        });
    }
    nodes
}

fn sandbox_node(
    record: SandboxRecord,
    daemon_client: Arc<dyn SandboxDaemonClient>,
    request_id: &str,
    timeout_ms: u64,
    services: &ManagerServices,
) -> Value {
    if record.state != SandboxState::Ready {
        return unavailable_node(
            &record,
            record.daemon.as_ref(),
            format!("sandbox lifecycle state is {}", record.state),
        );
    }
    let Some(endpoint) = record.daemon.clone() else {
        return unavailable_node(&record, None, "sandbox daemon endpoint is unavailable");
    };
    let request = sandbox_snapshot_request(&record, request_id);
    let mut node =
        match daemon_client.invoke(&endpoint, request, Some(Duration::from_millis(timeout_ms))) {
            Ok(response) => {
                node_from_daemon_response(&record, &endpoint, response.into_json_value())
            }
            Err(error) => unavailable_node(&record, Some(&endpoint), error.to_string()),
        };
    if let Ok(latest) = latest_resource_value(services, &record.id) {
        if let Some(object) = node.as_object_mut() {
            object.insert(
                "resources".to_owned(),
                json!({ "latest": latest, "history": [] }),
            );
        }
    }
    node
}

fn sandbox_snapshot_request(record: &SandboxRecord, request_id: &str) -> OperationRequest {
    OperationRequest::new(
        SNAPSHOT_SPEC.name,
        format!(
            "{}:{}:observability_snapshot",
            request_id,
            record.id.as_str()
        ),
        OperationScope::sandbox(record.id.as_str()),
        json!({}),
    )
}

fn node_from_daemon_response(
    record: &SandboxRecord,
    endpoint: &SandboxDaemonEndpoint,
    value: Value,
) -> Value {
    if let Some(error) = value.get("error") {
        return unavailable_node(record, Some(endpoint), response_error_message(error));
    }
    let Value::Object(mut object) = value else {
        return unavailable_node(
            record,
            Some(endpoint),
            "daemon snapshot response was not an object",
        );
    };
    object.insert("sandbox_id".to_owned(), json!(record.id.as_str()));
    object.insert("lifecycle_state".to_owned(), json!(record.state.as_str()));
    normalize_availability(&mut object);
    object
        .entry("errors".to_owned())
        .or_insert_with(|| json!([]));
    object
        .entry("daemon".to_owned())
        .or_insert_with(|| daemon_value(Some(endpoint)));
    object
        .entry("resources".to_owned())
        .or_insert_with(empty_resources_value);
    object
        .entry("workspaces".to_owned())
        .or_insert_with(|| json!([]));
    Value::Object(object)
}

fn normalize_availability(object: &mut Map<String, Value>) {
    match object.get("availability").and_then(Value::as_str) {
        Some("available" | "partial" | "unavailable") => {}
        _ => {
            object.insert("availability".to_owned(), json!("partial"));
            push_node_error(object, "daemon snapshot availability was malformed");
        }
    }
}

fn unavailable_node(
    record: &SandboxRecord,
    endpoint: Option<&SandboxDaemonEndpoint>,
    error: impl Into<String>,
) -> Value {
    json!({
        "sandbox_id": record.id.as_str(),
        "lifecycle_state": record.state.as_str(),
        "availability": "unavailable",
        "sampled_at_unix_ms": Value::Null,
        "errors": [bound_node_error(error.into())],
        "daemon": daemon_value(endpoint),
        "resources": empty_resources_value(),
        "workspaces": [],
    })
}

fn daemon_value(endpoint: Option<&SandboxDaemonEndpoint>) -> Value {
    json!({
        "host": endpoint.map(|endpoint| endpoint.host.clone()),
        "port": endpoint.map(|endpoint| endpoint.port),
        "daemon_pid": Value::Null,
        "runtime_dir": Value::Null,
    })
}

fn empty_resources_value() -> Value {
    json!({
        "latest": Value::Null,
        "history": [],
    })
}

fn response_error_message(error: &Value) -> String {
    error
        .get("message")
        .and_then(Value::as_str)
        .unwrap_or("daemon returned an error response")
        .to_owned()
}

fn push_node_error(object: &mut Map<String, Value>, error: impl Into<String>) {
    let error = json!(bound_node_error(error.into()));
    match object.get_mut("errors").and_then(Value::as_array_mut) {
        Some(errors) => errors.push(error),
        None => {
            object.insert("errors".to_owned(), json!([error]));
        }
    }
}

fn bound_node_error(value: String) -> String {
    if value.len() <= MAX_NODE_ERROR_BYTES {
        return value;
    }
    let mut end = MAX_NODE_ERROR_BYTES;
    while !value.is_char_boundary(end) {
        end = end.saturating_sub(1);
    }
    value[..end].to_owned()
}
