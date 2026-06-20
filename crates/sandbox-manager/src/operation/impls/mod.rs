pub(crate) mod create_sandbox;
pub(crate) mod describe_daemon_operations;
pub(crate) mod describe_manager_operations;
pub(crate) mod destroy_sandbox;
pub(crate) mod inspect_sandbox;
pub(crate) mod invoke_sandbox_daemon;
pub(crate) mod list_sandboxes;
pub(crate) mod start_sandbox_daemon;
pub(crate) mod stop_sandbox_daemon;

use serde_json::{json, Map, Value};

use crate::{
    ManagerError, ManagerResult, SandboxDaemonEndpoint, SandboxId, SandboxRecord, SandboxState,
};

use self::invoke_sandbox_daemon as invoke_daemon_impl;
use self::start_sandbox_daemon as start_daemon_impl;
use self::stop_sandbox_daemon as stop_daemon_impl;
use super::dispatch::ManagerOperationEntry;
use super::specs;

pub(crate) const OPERATIONS: &[ManagerOperationEntry] = &[
    ManagerOperationEntry::new(&specs::CREATE_SANDBOX, create_sandbox::dispatch),
    ManagerOperationEntry::new(&specs::DESTROY_SANDBOX, destroy_sandbox::dispatch),
    ManagerOperationEntry::new(&specs::LIST_SANDBOXES, list_sandboxes::dispatch),
    ManagerOperationEntry::new(&specs::INSPECT_SANDBOX, inspect_sandbox::dispatch),
    ManagerOperationEntry::new(&specs::START_SANDBOX_DAEMON, start_daemon_impl::dispatch),
    ManagerOperationEntry::new(&specs::STOP_SANDBOX_DAEMON, stop_daemon_impl::dispatch),
    ManagerOperationEntry::new(
        &specs::DESCRIBE_MANAGER_OPERATIONS,
        describe_manager_operations::dispatch,
    ),
    ManagerOperationEntry::new(
        &specs::DESCRIBE_DAEMON_OPERATIONS,
        describe_daemon_operations::dispatch,
    ),
    ManagerOperationEntry::new(&specs::INVOKE_SANDBOX_DAEMON, invoke_daemon_impl::dispatch),
];

pub(crate) const fn operation_entries() -> &'static [ManagerOperationEntry] {
    OPERATIONS
}

pub(crate) fn sandbox_id(
    request: &sandbox_protocol::Request<'_>,
) -> Result<SandboxId, sandbox_protocol::Response> {
    request
        .required_string("sandbox_id")
        .and_then(|value| SandboxId::new(value).map_err(ManagerError::into_response))
}

pub(crate) fn ready_record(
    services: &super::dispatch::ManagerServices,
    id: &SandboxId,
) -> ManagerResult<SandboxRecord> {
    let record = services.store.inspect(id)?;
    if record.state != SandboxState::Ready {
        return Err(ManagerError::InvalidStateTransition {
            id: id.clone(),
            from: record.state,
            to: SandboxState::Ready,
        });
    }
    Ok(record)
}

pub(crate) fn endpoint(
    services: &super::dispatch::ManagerServices,
    id: &SandboxId,
) -> ManagerResult<SandboxDaemonEndpoint> {
    let record = ready_record(services, id)?;
    record
        .daemon
        .ok_or_else(|| ManagerError::DaemonUnavailable { id: id.clone() })
}

pub(crate) fn request_object(
    request: &sandbox_protocol::Request<'_>,
    field: &str,
) -> Result<Map<String, Value>, sandbox_protocol::Response> {
    let Some(value) = request.args.get(field) else {
        return Err(request.invalid_argument(format!("{field} is required")));
    };
    let Some(object) = value.as_object() else {
        return Err(request.invalid_argument(format!("{field} must be an object")));
    };
    Ok(object.clone())
}

pub(crate) fn records_value(records: Vec<SandboxRecord>) -> Value {
    json!({
        "sandboxes": records.into_iter().map(record_value).collect::<Vec<_>>(),
    })
}

pub(crate) fn record_value(record: SandboxRecord) -> Value {
    json!({
        "id": record.id.as_str(),
        "state": record.state.as_str(),
        "daemon": record.daemon.map(endpoint_value),
    })
}

fn endpoint_value(endpoint: SandboxDaemonEndpoint) -> Value {
    json!({
        "socket_path": endpoint.socket_path.to_string_lossy(),
        "auth_token_configured": endpoint.auth_token.as_ref().is_some_and(|token| !token.is_empty()),
    })
}
