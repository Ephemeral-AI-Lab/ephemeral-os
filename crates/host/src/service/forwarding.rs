use serde_json::Value;

use super::forward::{forward_request, ForwardError, ForwardRequestInput};
use super::{HostForwardRequest, SandboxHost};

impl SandboxHost {
    pub fn forward(&self, request: HostForwardRequest<'_>) -> Option<Result<Value, ForwardError>> {
        let HostForwardRequest {
            sandbox_id,
            mutates_state,
            op,
            invocation_id,
            args,
        } = request;
        let record = self.registry.get(sandbox_id)?;
        Some(forward_request(ForwardRequestInput {
            record,
            config: &self.config,
            mutates_state,
            op,
            invocation_id,
            args,
        }))
    }
}
