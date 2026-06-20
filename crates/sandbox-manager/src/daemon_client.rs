use crate::{ManagerResult, SandboxDaemonEndpoint};

pub trait SandboxDaemonClient: Send + Sync {
    fn describe_operations(
        &self,
        endpoint: &SandboxDaemonEndpoint,
    ) -> ManagerResult<sandbox_protocol::OperationCatalog>;

    fn invoke(
        &self,
        endpoint: &SandboxDaemonEndpoint,
        request: sandbox_protocol::OwnedRequest,
    ) -> ManagerResult<sandbox_protocol::Response>;
}
