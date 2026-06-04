//! Pure plugin-dispatch helper: build payload -> call dynamic daemon op.

use eos_types::{JsonObject, SandboxId};
use serde_json::Value;

use crate::error::SandboxApiError;
use crate::models::{Intent, SandboxRequestBase};
use crate::tool_api::parse::daemon_request_identity_fields;
use crate::transport::SandboxTransport;

/// A catalog plugin operation request.
#[derive(Debug, Clone)]
pub struct PluginDispatchRequest {
    /// Shared sandbox caller identity for this tool call.
    pub base: SandboxRequestBase,
    /// Plugin id, for example `lsp`.
    pub plugin_id: String,
    /// Plugin operation name, for example `hover`.
    pub op_name: String,
    /// Declared sandbox execution intent.
    pub intent: Intent,
    /// Repository root visible to the plugin runtime.
    pub workspace_root: String,
    /// Model-facing tool arguments.
    pub args: JsonObject,
    /// Daemon dispatch timeout in seconds.
    pub timeout_s: u32,
}

/// Dispatch one catalog plugin operation through the sandbox daemon.
///
/// The dynamic daemon op is `plugin.<plugin_id>.<op_name>`. The caller supplies
/// `args` as the model-facing tool payload; this helper adds the standard daemon
/// caller identity plus plugin intent/workspace metadata.
pub async fn plugin_dispatch(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    request: PluginDispatchRequest,
) -> Result<JsonObject, SandboxApiError> {
    let mut payload = daemon_request_identity_fields(&request.base);
    payload.extend(request.args);
    payload.insert(
        "intent".to_owned(),
        Value::String(request.intent.as_wire().to_owned()),
    );
    payload.insert(
        "workspace_root".to_owned(),
        Value::String(request.workspace_root),
    );
    let op = format!("plugin.{}.{}", request.plugin_id, request.op_name);
    transport
        .call_dynamic(sandbox_id, &op, payload, request.timeout_s)
        .await
}
