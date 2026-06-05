//! Runtime implementation of the isolated-workspace model-tool port.

use std::collections::BTreeMap;
use std::fmt;
use std::sync::Arc;

use async_trait::async_trait;
use eos_sandbox_api::{
    enter_isolated_workspace, exit_isolated_workspace, EnterIsolatedWorkspaceRequest,
    EnterIsolatedWorkspaceResult, ExitIsolatedWorkspaceRequest, ExitIsolatedWorkspaceResult,
    LifecycleError, SandboxApiError, SandboxRequestBase, SandboxTransport,
};
use eos_sandbox_host::DEFAULT_LAYER_STACK_ROOT;
use eos_tools::ports::Sealed;
use eos_tools::{IsolatedWorkspacePort, ToolError, ToolResult};
use eos_types::{AgentRunId, SandboxId};
use serde_json::{json, Value};

/// Runtime bridge from `eos-tools` lifecycle calls to the sandbox daemon API.
pub(crate) struct RuntimeIsolatedWorkspace {
    transport: Arc<dyn SandboxTransport>,
}

impl RuntimeIsolatedWorkspace {
    /// Build a runtime isolated-workspace port over the shared sandbox transport.
    pub(crate) fn new(transport: Arc<dyn SandboxTransport>) -> Self {
        Self { transport }
    }
}

impl fmt::Debug for RuntimeIsolatedWorkspace {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("RuntimeIsolatedWorkspace")
            .finish_non_exhaustive()
    }
}

impl Sealed for RuntimeIsolatedWorkspace {}

#[async_trait]
impl IsolatedWorkspacePort for RuntimeIsolatedWorkspace {
    async fn enter(
        &self,
        agent_run_id: &AgentRunId,
        sandbox_id: &SandboxId,
        layer_stack_root: &str,
    ) -> Result<ToolResult, ToolError> {
        let request = EnterIsolatedWorkspaceRequest {
            base: request_base(agent_run_id, "enter isolated workspace"),
            layer_stack_root: effective_layer_stack_root(layer_stack_root),
        };
        let result = match enter_isolated_workspace(&*self.transport, sandbox_id, &request).await {
            Ok(result) => result,
            Err(err) => return render_enter_failure(&err),
        };
        render_enter_result(&result)
    }

    async fn exit(
        &self,
        agent_run_id: &AgentRunId,
        sandbox_id: &SandboxId,
        grace_s: f64,
    ) -> Result<ToolResult, ToolError> {
        let request = ExitIsolatedWorkspaceRequest {
            base: request_base(agent_run_id, "exit isolated workspace"),
            grace_s,
        };
        let result = match exit_isolated_workspace(&*self.transport, sandbox_id, &request).await {
            Ok(result) => result,
            Err(err) => return render_exit_failure(&err),
        };
        render_exit_result(&result)
    }
}

fn request_base(agent_run_id: &AgentRunId, description: &str) -> SandboxRequestBase {
    let agent_run_id = agent_run_id.as_str().to_owned();
    SandboxRequestBase {
        caller: eos_sandbox_api::SandboxCaller {
            caller_id: agent_run_id.clone(),
            run_id: agent_run_id.clone(),
            agent_run_id,
            task_id: String::new(),
            request_id: String::new(),
            attempt_id: String::new(),
            workflow_id: String::new(),
            tool_id: None,
        },
        description: description.to_owned(),
        invocation_id: None,
    }
}

fn effective_layer_stack_root(layer_stack_root: &str) -> String {
    if layer_stack_root.is_empty() {
        DEFAULT_LAYER_STACK_ROOT.to_owned()
    } else {
        layer_stack_root.to_owned()
    }
}

fn render_enter_result(result: &EnterIsolatedWorkspaceResult) -> Result<ToolResult, ToolError> {
    render_lifecycle(
        result.base.success,
        &json!({
            "success": result.base.success,
            "manifest_version": result.manifest_version,
            "manifest_root_hash": result.manifest_root_hash,
            "error": lifecycle_error_value(result.base.error.as_ref()),
        }),
    )
}

fn render_exit_result(result: &ExitIsolatedWorkspaceResult) -> Result<ToolResult, ToolError> {
    render_lifecycle(
        result.base.success,
        &json!({
            "success": result.base.success,
            "evicted_upperdir_bytes": result.evicted_upperdir_bytes,
            "lifetime_s": result.lifetime_s,
            "phases_ms": result.phases_ms,
            "error": lifecycle_error_value(result.base.error.as_ref()),
        }),
    )
}

fn render_enter_failure(error: &SandboxApiError) -> Result<ToolResult, ToolError> {
    render_lifecycle(
        false,
        &json!({
            "success": false,
            "manifest_version": "",
            "manifest_root_hash": "",
            "error": lifecycle_error_value(Some(&lifecycle_error_from_api(error))),
        }),
    )
}

fn render_exit_failure(error: &SandboxApiError) -> Result<ToolResult, ToolError> {
    render_lifecycle(
        false,
        &json!({
            "success": false,
            "evicted_upperdir_bytes": 0,
            "lifetime_s": 0.0,
            "phases_ms": {},
            "error": lifecycle_error_value(Some(&lifecycle_error_from_api(error))),
        }),
    )
}

fn render_lifecycle(success: bool, payload: &Value) -> Result<ToolResult, ToolError> {
    let output = serde_json::to_string_pretty(payload).map_err(|err| {
        ToolError::Internal(format!("failed to serialize lifecycle result: {err}"))
    })?;
    Ok(if success {
        ToolResult::ok(output)
    } else {
        ToolResult::error(output)
    })
}

fn lifecycle_error_value(error: Option<&LifecycleError>) -> Value {
    match error {
        Some(error) => json!({
            "kind": error.kind,
            "message": error.message,
            "details": error.details,
        }),
        None => Value::Null,
    }
}

fn lifecycle_error_from_api(error: &SandboxApiError) -> LifecycleError {
    let fallback = match error {
        SandboxApiError::Decode { .. } => "decode_error",
        _ => "internal_error",
    };
    LifecycleError {
        kind: error.code().unwrap_or(fallback).to_owned(),
        message: error.message().to_owned(),
        details: BTreeMap::new(),
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Mutex;

    use eos_sandbox_api::{DaemonOp, SandboxTransport};
    use eos_types::JsonObject;

    use super::*;

    struct RecordingTransport {
        calls: Mutex<Vec<(DaemonOp, JsonObject, u32)>>,
        response: JsonObject,
    }

    impl RecordingTransport {
        fn new(response: Value) -> Self {
            Self {
                calls: Mutex::new(Vec::new()),
                response: object(response),
            }
        }
    }

    #[async_trait]
    impl SandboxTransport for RecordingTransport {
        async fn call(
            &self,
            _sandbox_id: &SandboxId,
            op: DaemonOp,
            payload: JsonObject,
            timeout_s: u32,
        ) -> Result<JsonObject, SandboxApiError> {
            self.calls
                .lock()
                .expect("calls lock")
                .push((op, payload, timeout_s));
            Ok(self.response.clone())
        }
    }

    #[tokio::test]
    async fn enter_defaults_layer_stack_root_and_renders_success() {
        let transport = Arc::new(RecordingTransport::new(json!({
            "success": true,
            "manifest_version": "v1",
            "manifest_root_hash": "hash-1"
        })));
        let adapter = RuntimeIsolatedWorkspace::new(transport.clone());
        let agent_run_id: AgentRunId = "agent-run-1".parse().expect("agent run id");
        let sandbox_id: SandboxId = "sandbox-1".parse().expect("sandbox id");

        let result = adapter
            .enter(&agent_run_id, &sandbox_id, "")
            .await
            .expect("enter");

        assert!(!result.is_error);
        let output: Value = serde_json::from_str(&result.output).expect("json output");
        assert_eq!(output["success"], json!(true));
        assert_eq!(output["manifest_root_hash"], json!("hash-1"));
        assert_eq!(output["error"], Value::Null);
        let calls = transport.calls.lock().expect("calls lock");
        assert_eq!(calls[0].0, DaemonOp::IsolatedWorkspaceEnter);
        assert_eq!(calls[0].1["caller_id"], json!("agent-run-1"));
        assert_eq!(calls[0].1["caller"]["run_id"], json!("agent-run-1"));
        assert_eq!(
            calls[0].1["caller"]["agent_run_id"],
            json!("agent-run-1")
        );
        assert_eq!(
            calls[0].1["layer_stack_root"],
            json!(DEFAULT_LAYER_STACK_ROOT)
        );
    }

    #[tokio::test]
    async fn exit_forwards_grace_and_renders_lifecycle_failure() {
        let transport = Arc::new(RecordingTransport::new(json!({
            "error": {
                "kind": "not_active",
                "message": "isolated workspace is not active",
                "details": {"caller_id": "agent-1"}
            }
        })));
        let adapter = RuntimeIsolatedWorkspace::new(transport.clone());
        let agent_run_id: AgentRunId = "agent-run-1".parse().expect("agent run id");
        let sandbox_id: SandboxId = "sandbox-1".parse().expect("sandbox id");

        let result = adapter
            .exit(&agent_run_id, &sandbox_id, 0.5)
            .await
            .expect("exit");

        assert!(result.is_error);
        let output: Value = serde_json::from_str(&result.output).expect("json output");
        assert_eq!(output["success"], json!(false));
        assert_eq!(output["error"]["kind"], json!("not_active"));
        let calls = transport.calls.lock().expect("calls lock");
        assert_eq!(calls[0].0, DaemonOp::IsolatedWorkspaceExit);
        assert_eq!(calls[0].1["caller_id"], json!("agent-run-1"));
        assert_eq!(calls[0].1["caller"]["run_id"], json!("agent-run-1"));
        assert_eq!(
            calls[0].1["caller"]["agent_run_id"],
            json!("agent-run-1")
        );
        assert_eq!(calls[0].1["grace_s"], json!(0.5));
    }

    fn object(value: Value) -> JsonObject {
        match value {
            Value::Object(map) => map,
            _ => JsonObject::new(),
        }
    }
}
