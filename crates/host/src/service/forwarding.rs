use serde_json::Value;

use super::forward::{forward_request, ForwardError, ForwardRequestInput};
use super::{HostForwardRequest, SandboxHost};

impl SandboxHost {
    pub fn forward_with_trace(
        &self,
        request: HostForwardRequest<'_>,
    ) -> Option<Result<Value, ForwardError>> {
        let HostForwardRequest {
            sandbox_id,
            mutates_state,
            op,
            invocation_id,
            args,
            trace,
        } = request;
        let record = self.registry.get(sandbox_id)?;
        Some(forward_request(ForwardRequestInput {
            record,
            config: &self.config,
            trace_store: &self.trace_store,
            trace_context: trace,
            mutates_state,
            op,
            invocation_id,
            args,
        }))
    }
}
