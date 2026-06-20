use std::sync::Arc;

use crate::{SandboxDaemonClient, SandboxDaemonInstaller, SandboxRuntime, SandboxStore};

#[derive(Clone, Copy)]
pub struct ManagerOperationEntry {
    pub spec: &'static sandbox_protocol::OperationSpec,
    pub dispatch: fn(&ManagerServices, sandbox_protocol::Request<'_>) -> sandbox_protocol::Response,
}

impl ManagerOperationEntry {
    #[must_use]
    pub const fn new(
        spec: &'static sandbox_protocol::OperationSpec,
        dispatch: fn(&ManagerServices, sandbox_protocol::Request<'_>) -> sandbox_protocol::Response,
    ) -> Self {
        Self { spec, dispatch }
    }
}

pub struct ManagerServices {
    pub store: Arc<SandboxStore>,
    pub runtime: Arc<dyn SandboxRuntime>,
    pub daemon_installer: Arc<dyn SandboxDaemonInstaller>,
    pub daemon_client: Arc<dyn SandboxDaemonClient>,
}

impl ManagerServices {
    #[must_use]
    pub fn new(
        store: Arc<SandboxStore>,
        runtime: Arc<dyn SandboxRuntime>,
        daemon_installer: Arc<dyn SandboxDaemonInstaller>,
        daemon_client: Arc<dyn SandboxDaemonClient>,
    ) -> Self {
        Self {
            store,
            runtime,
            daemon_installer,
            daemon_client,
        }
    }
}

#[must_use]
pub fn dispatch_operation(
    services: &ManagerServices,
    request: sandbox_protocol::Request<'_>,
) -> sandbox_protocol::Response {
    super::impls::operation_entries()
        .iter()
        .find(|entry| entry.spec.name == request.name)
        .map_or_else(
            || sandbox_protocol::Response::unknown_op(&request),
            |entry| (entry.dispatch)(services, request),
        )
}
