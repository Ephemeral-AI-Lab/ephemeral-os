use sandbox_operation_contract::{OperationRequest, OperationResponse, OperationScope};

use crate::{ManagerError, ManagerServices, SandboxDaemonEndpoint, SandboxId, SandboxState};

pub(crate) fn forward_sandbox_request(
    services: &ManagerServices,
    request: OperationRequest,
) -> Result<OperationResponse, ManagerError> {
    let id = sandbox_id(&request.scope)?;
    let endpoint = daemon_endpoint(services, &id)?;
    services.daemon_client.invoke(&endpoint, request, None)
}

fn sandbox_id(scope: &OperationScope) -> Result<SandboxId, ManagerError> {
    match scope {
        OperationScope::Sandbox { sandbox_id } => SandboxId::new(sandbox_id.clone()),
        OperationScope::System => Err(ManagerError::InvalidSandboxId {
            value: "system".to_owned(),
        }),
    }
}

fn daemon_endpoint(
    services: &ManagerServices,
    id: &SandboxId,
) -> Result<SandboxDaemonEndpoint, ManagerError> {
    let record = services.store.inspect(id)?;
    if record.state != SandboxState::Ready {
        return Err(ManagerError::InvalidStateTransition {
            id: id.clone(),
            from: record.state,
            to: SandboxState::Ready,
        });
    }
    record
        .daemon
        .ok_or_else(|| ManagerError::DaemonUnavailable { id: id.clone() })
}
