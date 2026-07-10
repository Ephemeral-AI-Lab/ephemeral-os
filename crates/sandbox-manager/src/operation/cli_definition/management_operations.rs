use std::path::PathBuf;

use sandbox_operation_catalog::manager::{
    CREATE_SANDBOX_SPEC, DESTROY_SANDBOX_SPEC, EXPORT_CHANGES_SPEC, INSPECT_SANDBOX_SPEC,
    LIST_SANDBOXES_SPEC, SQUASH_LAYERSTACKS_SPEC,
};
use sandbox_operation_catalog::observability::SNAPSHOT_SPEC;
use sandbox_operation_contract::{OperationRequest, OperationResponse};
use serde_json::{json, Value};

use crate::operation::dispatch::ManagerOperationEntry;
use crate::operation::management::{
    create_sandbox, destroy_sandbox, dispatch_export_changes, dispatch_squash_layerstacks,
    inspect_sandbox, list_sandboxes, observability_snapshot, CreateSandboxInput, SnapshotOptions,
};
use crate::operation::ManagerServices;
use crate::{
    ManagerError, ProgressSink, SandboxDaemonEndpoint, SandboxHttpEndpoint, SandboxId,
    SandboxRecord,
};

const OPERATIONS: &[ManagerOperationEntry] = &[
    ManagerOperationEntry::new(&CREATE_SANDBOX_SPEC, dispatch_create_sandbox),
    ManagerOperationEntry::new(&DESTROY_SANDBOX_SPEC, dispatch_destroy_sandbox),
    ManagerOperationEntry::new(&SNAPSHOT_SPEC, dispatch_observability_snapshot),
    ManagerOperationEntry::new(&LIST_SANDBOXES_SPEC, dispatch_list_sandboxes),
    ManagerOperationEntry::new(&INSPECT_SANDBOX_SPEC, dispatch_inspect_sandbox),
    ManagerOperationEntry::new(&SQUASH_LAYERSTACKS_SPEC, dispatch_squash_layerstacks),
    ManagerOperationEntry::new(&EXPORT_CHANGES_SPEC, dispatch_export_changes),
];

pub(crate) fn operation_entries() -> &'static [ManagerOperationEntry] {
    OPERATIONS
}

fn dispatch_create_sandbox(
    services: &ManagerServices,
    request: &OperationRequest,
) -> OperationResponse {
    dispatch_create_sandbox_with_progress(services, request, &ProgressSink::noop())
}

pub(crate) fn dispatch_create_sandbox_with_progress(
    services: &ManagerServices,
    request: &OperationRequest,
    progress: &ProgressSink,
) -> OperationResponse {
    let image = match image(request) {
        Ok(image) => image,
        Err(response) => return response,
    };
    let workspace_root = match workspace_root(request) {
        Ok(workspace_root) => workspace_root,
        Err(response) => return response,
    };
    let count = match count(request) {
        Ok(count) => count,
        Err(response) => return response,
    };
    match create_sandbox(
        services,
        CreateSandboxInput {
            image,
            workspace_root,
            count,
        },
        progress,
    ) {
        Ok(mut records) if records.len() == 1 => {
            OperationResponse::ok(record_value(records.remove(0)))
        }
        Ok(records) => OperationResponse::ok(records_value(records)),
        Err(error) => error.into_response(),
    }
}

fn dispatch_destroy_sandbox(
    services: &ManagerServices,
    request: &OperationRequest,
) -> OperationResponse {
    let id = match sandbox_id(request) {
        Ok(id) => id,
        Err(response) => return response,
    };
    match destroy_sandbox(services, id) {
        Ok(record) => OperationResponse::ok(record_value(record)),
        Err(error) => error.into_response(),
    }
}

fn dispatch_inspect_sandbox(
    services: &ManagerServices,
    request: &OperationRequest,
) -> OperationResponse {
    let id = match sandbox_id(request) {
        Ok(id) => id,
        Err(response) => return response,
    };
    match inspect_sandbox(services, &id) {
        Ok(record) => OperationResponse::ok(record_value(record)),
        Err(error) => error.into_response(),
    }
}

fn dispatch_list_sandboxes(
    services: &ManagerServices,
    _request: &OperationRequest,
) -> OperationResponse {
    match list_sandboxes(services) {
        Ok(records) => OperationResponse::ok(records_value(records)),
        Err(error) => error.into_response(),
    }
}

fn dispatch_observability_snapshot(
    services: &ManagerServices,
    request: &OperationRequest,
) -> OperationResponse {
    let options = match snapshot_options(request) {
        Ok(options) => options,
        Err(response) => return response,
    };
    match observability_snapshot(services, options, &request.request_id) {
        Ok(sandboxes) => OperationResponse::ok(json!({ "sandboxes": sandboxes })),
        Err(error) => error.into_response(),
    }
}

fn sandbox_id(request: &OperationRequest) -> Result<SandboxId, OperationResponse> {
    request
        .required_string("sandbox_id")
        .and_then(|value| SandboxId::new(value).map_err(ManagerError::into_response))
}

fn workspace_root(request: &OperationRequest) -> Result<PathBuf, OperationResponse> {
    let raw = request.required_string("workspace_root")?;
    let path = PathBuf::from(&raw);
    if !path.is_absolute() {
        return Err(ManagerError::InvalidWorkspaceRoot { value: raw }.into_response());
    }
    Ok(path)
}

fn image(request: &OperationRequest) -> Result<String, OperationResponse> {
    let image = request.required_string("image")?;
    if image.trim().is_empty() {
        return Err(ManagerError::InvalidImage { value: image }.into_response());
    }
    Ok(image)
}

fn count(request: &OperationRequest) -> Result<usize, OperationResponse> {
    let value = request.optional_u64("count")?.unwrap_or(1);
    if value == 0 {
        return Err(ManagerError::InvalidSandboxCount { value }.into_response());
    }
    usize::try_from(value).map_err(|_| ManagerError::InvalidSandboxCount { value }.into_response())
}

fn snapshot_options(request: &OperationRequest) -> Result<SnapshotOptions, OperationResponse> {
    let sandbox_id = request
        .optional_string("sandbox_id")?
        .map(SandboxId::new)
        .transpose()
        .map_err(ManagerError::into_response)?;
    Ok(SnapshotOptions { sandbox_id })
}

fn records_value(records: Vec<SandboxRecord>) -> Value {
    json!({
        "sandboxes": records.into_iter().map(record_value).collect::<Vec<_>>(),
    })
}

fn record_value(record: SandboxRecord) -> Value {
    json!({
        "id": record.id.as_str(),
        "workspace_root": record.workspace_root.to_string_lossy(),
        "state": record.state.as_str(),
        "daemon": record.daemon.map(endpoint_value),
        "daemon_http": record.daemon_http.map(http_endpoint_value),
        "shared_base": record.shared_base.map(shared_base_value),
    })
}

fn shared_base_value(shared_base: crate::SharedBaseMount) -> Value {
    json!({
        "source": shared_base.source.to_string_lossy(),
        "target": shared_base.target.to_string_lossy(),
        "root_hash": shared_base.root_hash,
        "readonly": shared_base.readonly,
    })
}

fn endpoint_value(endpoint: SandboxDaemonEndpoint) -> Value {
    json!({
        "host": endpoint.host,
        "port": endpoint.port,
    })
}

fn http_endpoint_value(endpoint: SandboxHttpEndpoint) -> Value {
    json!({
        "host": endpoint.host,
        "port": endpoint.port,
    })
}
