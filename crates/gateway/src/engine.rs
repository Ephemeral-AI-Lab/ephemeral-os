use anyhow::Result;
use serde_json::{json, Value};

use host::{ForwardError, HostForwardRequest, SandboxHost, SandboxStatus};

pub(crate) trait Engine: Send + Sync {
    fn acquire(&self, args: &Value) -> Result<String>;
    fn release(&self, sandbox_id: &str, args: &Value) -> Result<bool>;
    fn status(&self, sandbox_id: &str) -> Option<Value>;
    fn list(&self) -> Vec<Value>;
    fn forward(&self, request: HostForwardRequest<'_>) -> Option<Result<Value, ForwardError>>;

    fn image_profiles_list(&self, _args: &Value) -> Result<Value> {
        anyhow::bail!("image profile listing is not available on this engine")
    }

    fn image_list(&self, _args: &Value) -> Result<Value> {
        anyhow::bail!("image listing is not available on this engine")
    }

    fn image_pull(&self, _args: &Value) -> Result<Value> {
        anyhow::bail!("image pull is not available on this engine")
    }

    fn container_list(&self, _args: &Value) -> Result<Value> {
        anyhow::bail!("container listing is not available on this engine")
    }

    fn container_start(&self, _args: &Value) -> Result<Value> {
        anyhow::bail!("container start is not available on this engine")
    }

    fn container_adopt(&self, _args: &Value) -> Result<Value> {
        anyhow::bail!("container adoption is not available on this engine")
    }

    fn container_stop(&self, _args: &Value) -> Result<Value> {
        anyhow::bail!("container stop is not available on this engine")
    }

    fn container_remove(&self, _args: &Value) -> Result<Value> {
        anyhow::bail!("container removal is not available on this engine")
    }
}

impl Engine for SandboxHost {
    fn acquire(&self, args: &Value) -> Result<String> {
        SandboxHost::acquire_with_args(self, args)
    }

    fn release(&self, sandbox_id: &str, args: &Value) -> Result<bool> {
        SandboxHost::release_with_args(self, sandbox_id, args)
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
        SandboxHost::forward(self, request)
    }

    fn image_profiles_list(&self, args: &Value) -> Result<Value> {
        SandboxHost::image_profiles_list(self, args)
    }

    fn image_list(&self, args: &Value) -> Result<Value> {
        SandboxHost::image_list(self, args)
    }

    fn image_pull(&self, args: &Value) -> Result<Value> {
        SandboxHost::image_pull(self, args)
    }

    fn container_list(&self, args: &Value) -> Result<Value> {
        SandboxHost::container_list(self, args)
    }

    fn container_start(&self, args: &Value) -> Result<Value> {
        SandboxHost::container_start(self, args)
    }

    fn container_adopt(&self, args: &Value) -> Result<Value> {
        SandboxHost::container_adopt(self, args)
    }

    fn container_stop(&self, args: &Value) -> Result<Value> {
        SandboxHost::container_stop(self, args)
    }

    fn container_remove(&self, args: &Value) -> Result<Value> {
        SandboxHost::container_remove(self, args)
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
