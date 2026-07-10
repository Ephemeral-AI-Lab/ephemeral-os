use std::time::Duration;

use sandbox_operation_contract::{OperationRequest, OperationResponse};

use crate::{ManagerError, SandboxDaemonEndpoint};

pub trait SandboxDaemonClient: Send + Sync {
    fn invoke(
        &self,
        endpoint: &SandboxDaemonEndpoint,
        request: OperationRequest,
        timeout_override: Option<Duration>,
    ) -> Result<OperationResponse, ManagerError>;
}
