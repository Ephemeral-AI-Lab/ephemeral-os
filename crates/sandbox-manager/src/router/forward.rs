use std::time::Duration;

use sandbox_protocol::{CliOperationScope, Request};

use crate::{ManagerError, ManagerServices, SandboxDaemonEndpoint, SandboxId, SandboxState};

pub(crate) fn forward_sandbox_request(
    services: &ManagerServices,
    request: Request,
) -> Result<sandbox_protocol::Response, ManagerError> {
    let id = sandbox_id(&request.scope)?;
    let endpoint = daemon_endpoint(services, &id)?;
    services.daemon_client.invoke_with_timeout(
        &endpoint,
        request,
        Duration::from_secs_f64(sandbox_protocol::ProtocolLimits::DEFAULT_REQUEST_READ_TIMEOUT_S),
    )
}

fn sandbox_id(scope: &CliOperationScope) -> Result<SandboxId, ManagerError> {
    match scope {
        CliOperationScope::Sandbox { sandbox_id } => SandboxId::new(sandbox_id.clone()),
        CliOperationScope::System => Err(ManagerError::InvalidSandboxId {
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
