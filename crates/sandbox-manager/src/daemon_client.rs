use crate::{ManagerError, SandboxDaemonEndpoint};

pub trait SandboxDaemonClient: Send + Sync {
    fn describe_operations(
        &self,
        endpoint: &SandboxDaemonEndpoint,
    ) -> Result<sandbox_protocol::OperationCatalog, ManagerError>;

    fn invoke(
        &self,
        endpoint: &SandboxDaemonEndpoint,
        request: sandbox_protocol::SandboxRequest,
    ) -> Result<sandbox_protocol::OperationResponse, ManagerError>;
}
