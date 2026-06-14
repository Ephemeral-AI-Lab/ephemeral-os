use anyhow::Result;
use serde_json::{json, Value};

use host::{ForwardError, ForwardTraceContext, HostForwardRequest, SandboxHost, SandboxStatus};

pub(crate) trait Engine: Send + Sync {
    fn acquire(&self, trace: &ForwardTraceContext, args: &Value) -> Result<String>;
    fn release(&self, sandbox_id: &str, trace: &ForwardTraceContext, args: &Value) -> Result<bool>;
    fn status(&self, sandbox_id: &str) -> Option<Value>;
    fn list(&self) -> Vec<Value>;
    fn forward(&self, request: HostForwardRequest<'_>) -> Option<Result<Value, ForwardError>>;

    fn trace_requests(&self, _trace: &ForwardTraceContext, _args: &Value) -> Result<Value> {
        anyhow::bail!("trace request listing is not available on this engine")
    }

    fn trace_show(&self, _trace: &ForwardTraceContext, _args: &Value) -> Result<Value> {
        anyhow::bail!("trace show is not available on this engine")
    }

    fn trace_verify(&self, _trace: &ForwardTraceContext, _args: &Value) -> Result<Value> {
        anyhow::bail!("trace verify is not available on this engine")
    }

    fn record_trace_event(
        &self,
        _sandbox_id: &str,
        _trace: &ForwardTraceContext,
        _module: &str,
        _event: &str,
        _details: Value,
    ) {
    }
}

impl Engine for SandboxHost {
    fn acquire(&self, trace: &ForwardTraceContext, args: &Value) -> Result<String> {
        SandboxHost::acquire_with_trace(self, trace, args)
    }

    fn release(&self, sandbox_id: &str, trace: &ForwardTraceContext, args: &Value) -> Result<bool> {
        SandboxHost::release_with_trace(self, sandbox_id, trace, args)
    }

    fn status(&self, sandbox_id: &str) -> Option<Value> {
        SandboxHost::status(self, sandbox_id).map(|status| status_value(&status, true))
    }

    fn list(&self) -> Vec<Value> {
        SandboxHost::list(self)
            .iter()
            .map(|status| status_value(status, false))
            .collect()
    }

    fn forward(&self, request: HostForwardRequest<'_>) -> Option<Result<Value, ForwardError>> {
        SandboxHost::forward_with_trace(self, request)
    }

    fn trace_requests(&self, trace: &ForwardTraceContext, args: &Value) -> Result<Value> {
        SandboxHost::trace_requests(self, trace, args)
    }

    fn trace_show(&self, trace: &ForwardTraceContext, args: &Value) -> Result<Value> {
        SandboxHost::trace_show(self, trace, args)
    }

    fn trace_verify(&self, trace: &ForwardTraceContext, args: &Value) -> Result<Value> {
        SandboxHost::trace_verify(self, trace, args)
    }

    fn record_trace_event(
        &self,
        sandbox_id: &str,
        trace: &ForwardTraceContext,
        module: &str,
        event: &str,
        details: Value,
    ) {
        SandboxHost::record_trace_event(self, sandbox_id, trace, module, event, details);
    }
}

fn status_value(status: &SandboxStatus, embed_daemon: bool) -> Value {
    let mut value = json!({
        "sandbox_id": status.sandbox_id,
        "container": status.container,
        "endpoint": status.endpoint.map(|addr| addr.to_string()),
        "created_by": status.created_by,
    });
    if embed_daemon {
        value["daemon"] = status.daemon.clone();
    }
    value
}
