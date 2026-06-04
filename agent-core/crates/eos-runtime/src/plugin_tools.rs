//! Runtime binding for catalog plugin tools.
//!
//! `eos-plugin-catalog` owns the declared model-facing specs. This module binds
//! those specs into real `eos-tools` executors that ensure the daemon manifest
//! for built-in plugin runtimes and then dispatch dynamic `plugin.<plugin>.<op>`
//! daemon operations.

use std::sync::{Arc, LazyLock};

use async_trait::async_trait;
use eos_llm_client::ToolSpec;
use eos_plugin_catalog::{plugin_tool_specs, PluginToolSpec};
use eos_sandbox_api::{Intent, PluginDispatchRequest, PluginEnsureRequest, SandboxRequestBase};
use eos_tools::{
    ExecutionMetadata, OutputShape, RegisteredTool, ToolError, ToolExecutor, ToolIntent, ToolKey,
    ToolRegistry, ToolResult,
};
use eos_types::JsonObject;
use serde_json::{json, Value};

const LSP_PLUGIN_ID: &str = "lsp";
const LSP_PLUGIN_VERSION: &str = "0.1.0";
const LSP_PLUGIN_DIGEST: &str = "builtin-lsp-pyright-v1";
const LSP_SERVICE_ID: &str = "pyright";
const LSP_SERVICE_PROFILE_DIGEST: &str = "builtin-lsp-pyright-service-v1";
const PLUGIN_DISPATCH_TIMEOUT_S: u32 = 150;
const PLUGIN_ENSURE_TIMEOUT_S: u32 = 150;
const PLUGIN_OP_TIMEOUT_MS: u64 = (PLUGIN_DISPATCH_TIMEOUT_S as u64) * 1_000;

static LSP_MANIFEST: LazyLock<JsonObject> = LazyLock::new(lsp_manifest);

/// Register every built-in plugin catalog tool into `registry`.
pub(crate) fn register_plugin_tools(registry: &mut ToolRegistry) {
    for spec in plugin_tool_specs() {
        registry.register(registered_plugin_tool(spec));
    }
}

fn registered_plugin_tool(spec: PluginToolSpec) -> RegisteredTool {
    let name = spec.name.as_str().to_owned();
    let parsed_name = split_plugin_tool_name(&name);
    let input_schema = match serde_json::to_value(spec.input_schema) {
        Ok(Value::Object(map)) => map,
        _ => JsonObject::new(),
    };
    let tool_spec = ToolSpec::new(name.clone(), spec.description, input_schema, None);
    RegisteredTool::new(
        ToolKey::dynamic(name),
        ToolIntent::from(spec.intent),
        false,
        tool_spec,
        OutputShape::Text,
        Arc::new(PluginToolExecutor {
            parsed_name,
            intent: spec.intent,
        }),
    )
}

fn split_plugin_tool_name(name: &str) -> Option<(String, String)> {
    split_plugin_tool_name_parts(name)
        .map(|(plugin_id, op_name)| (plugin_id.to_owned(), op_name.to_owned()))
}

fn split_plugin_tool_name_parts(name: &str) -> Option<(&str, &str)> {
    name.split_once('.')
        .filter(|(plugin_id, op_name)| !plugin_id.is_empty() && !op_name.is_empty())
}

#[derive(Debug)]
struct PluginToolExecutor {
    parsed_name: Option<(String, String)>,
    intent: Intent,
}

#[async_trait]
impl ToolExecutor for PluginToolExecutor {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let Some((plugin_id, op_name)) = &self.parsed_name else {
            return Err(ToolError::Internal(
                "catalog plugin tool name must be <plugin>.<op>".to_owned(),
            ));
        };
        let sandbox_id = ctx.require_sandbox_id()?;
        let base = SandboxRequestBase {
            caller: ctx.caller.clone(),
            description: format!("plugin {plugin_id}.{op_name}"),
            invocation_id: ctx.sandbox_invocation_id.clone(),
        };
        ensure_plugin_runtime(plugin_id, &base, ctx).await?;
        let response = eos_sandbox_api::plugin_dispatch(
            &*ctx.transport,
            sandbox_id,
            PluginDispatchRequest {
                base,
                plugin_id: plugin_id.clone(),
                op_name: op_name.clone(),
                intent: self.intent,
                workspace_root: ctx.repo_root.clone(),
                args: input.clone(),
                timeout_s: PLUGIN_DISPATCH_TIMEOUT_S,
            },
        )
        .await?;
        Ok(plugin_result(&response))
    }
}

async fn ensure_plugin_runtime(
    plugin_id: &str,
    base: &SandboxRequestBase,
    ctx: &ExecutionMetadata,
) -> Result<(), ToolError> {
    if plugin_id != LSP_PLUGIN_ID {
        return Ok(());
    }
    let sandbox_id = ctx.require_sandbox_id()?;
    eos_sandbox_api::plugin_ensure(
        &*ctx.transport,
        sandbox_id,
        PluginEnsureRequest {
            base: base.clone(),
            workspace_root: ctx.repo_root.clone(),
            manifest: LSP_MANIFEST.clone(),
            start_services: true,
            timeout_s: PLUGIN_ENSURE_TIMEOUT_S,
        },
    )
    .await?;
    Ok(())
}

fn lsp_manifest() -> JsonObject {
    json_object(json!({
        "plugin_id": LSP_PLUGIN_ID,
        "plugin_version": LSP_PLUGIN_VERSION,
        "plugin_digest": LSP_PLUGIN_DIGEST,
        "services": [{
            "service_id": LSP_SERVICE_ID,
            "service_profile_digest": LSP_SERVICE_PROFILE_DIGEST,
            "service_mode": "workspace_snapshot_refresh",
            "refresh_strategy": "remount_workspace_and_notify",
            "command": ["pyright-langserver", "--stdio"],
            "ppc_protocol_version": 1
        }],
        "operations": lsp_manifest_operations()
    }))
}

fn lsp_manifest_operations() -> Vec<Value> {
    plugin_tool_specs()
        .into_iter()
        .filter_map(|spec| {
            let (plugin_id, op_name) = split_plugin_tool_name_parts(spec.name.as_str())?;
            (plugin_id == LSP_PLUGIN_ID).then(|| lsp_operation(op_name, spec.intent))
        })
        .collect()
}

fn lsp_operation(op_name: &str, intent: Intent) -> Value {
    let mut operation = JsonObject::new();
    operation.insert("op_name".to_owned(), Value::String(op_name.to_owned()));
    operation.insert(
        "intent".to_owned(),
        Value::String(intent.as_wire().to_owned()),
    );
    operation.insert(
        "service_id".to_owned(),
        Value::String(LSP_SERVICE_ID.to_owned()),
    );
    operation.insert("timeout_ms".to_owned(), Value::from(PLUGIN_OP_TIMEOUT_MS));
    if intent == Intent::WriteAllowed {
        operation.insert("auto_workspace_overlay".to_owned(), Value::Bool(false));
    }
    Value::Object(operation)
}

fn json_object(value: Value) -> JsonObject {
    match value {
        Value::Object(object) => object,
        _ => unreachable!("plugin manifest literal must be a JSON object"),
    }
}

fn plugin_result(response: &JsonObject) -> ToolResult {
    let is_error = response.get("success") == Some(&Value::Bool(false));
    let output = serde_json::to_string(response)
        .unwrap_or_else(|err| format!(r#"{{"success":false,"error":"{err}"}}"#));
    if is_error {
        ToolResult::error(output)
    } else {
        ToolResult::ok(output)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    use eos_llm_client::Message;
    use eos_sandbox_api::{DaemonOp, SandboxApiError, SandboxCaller, SandboxTransport};
    use eos_skills::SkillRegistry;
    use eos_state::{
        ExecutionTaskOutcome, Request, RequestStore, Sealed, StoreError, Task, TaskStatus,
        TaskStore,
    };
    use eos_types::{RequestId, SandboxId, TaskId};

    #[test]
    fn registers_lsp_plugin_tools() {
        let mut registry = ToolRegistry::new();
        register_plugin_tools(&mut registry);
        let hover = registry.get_wire("lsp.hover").expect("hover registered");
        assert_eq!(hover.name.as_str(), "lsp.hover");
        assert_eq!(hover.intent, ToolIntent::ReadOnly);
        assert!(!hover.is_terminal);
        assert!(registry.get_wire("lsp.rename").is_some());
    }

    #[tokio::test]
    async fn lsp_executor_ensures_manifest_before_dispatch() {
        let transport = Arc::new(RecordingTransport::default());
        let ctx = metadata_with(transport.clone());
        let executor = PluginToolExecutor {
            parsed_name: Some((LSP_PLUGIN_ID.to_owned(), "hover".to_owned())),
            intent: Intent::ReadOnly,
        };
        let input = json_object(json!({
            "file_path": "src/main.py",
            "line": 2,
            "character": 4
        }));

        let _result = executor.execute(&input, &ctx).await.expect("execute");

        let calls = transport.calls.lock().expect("calls lock").clone();
        assert_eq!(calls.len(), 2);
        assert_eq!(calls[0].op, "api.plugin.ensure");
        assert_eq!(calls[0].timeout_s, PLUGIN_ENSURE_TIMEOUT_S);
        assert_eq!(
            calls[0].payload.get("workspace_root"),
            Some(&json!("/repo"))
        );
        assert_eq!(calls[0].payload.get("start_services"), Some(&json!(true)));
        assert_eq!(calls[0].payload.get("invocation_id"), Some(&json!("inv-1")));

        let manifest = calls[0].payload.get("manifest").expect("manifest");
        assert_eq!(manifest.get("plugin_id"), Some(&json!(LSP_PLUGIN_ID)));
        assert_eq!(
            manifest.get("plugin_digest"),
            Some(&json!(LSP_PLUGIN_DIGEST))
        );
        assert_eq!(
            manifest
                .get("services")
                .and_then(Value::as_array)
                .and_then(|services| services.first())
                .and_then(|service| service.get("command")),
            Some(&json!(["pyright-langserver", "--stdio"]))
        );
        let operations = manifest
            .get("operations")
            .and_then(Value::as_array)
            .expect("operations");
        let catalog_lsp_tool_count = plugin_tool_specs()
            .into_iter()
            .filter(|spec| {
                split_plugin_tool_name_parts(spec.name.as_str())
                    .is_some_and(|(plugin_id, _)| plugin_id == LSP_PLUGIN_ID)
            })
            .count();
        assert_eq!(operations.len(), catalog_lsp_tool_count);
        assert!(operations.iter().any(|operation| {
            operation.get("op_name") == Some(&json!("rename"))
                && operation.get("intent") == Some(&json!("write_allowed"))
                && operation.get("service_id") == Some(&json!(LSP_SERVICE_ID))
                && operation.get("auto_workspace_overlay") == Some(&json!(false))
        }));

        assert_eq!(calls[1].op, "plugin.lsp.hover");
        assert_eq!(calls[1].timeout_s, PLUGIN_DISPATCH_TIMEOUT_S);
        assert_eq!(
            calls[1].payload.get("file_path"),
            Some(&json!("src/main.py"))
        );
        assert_eq!(calls[1].payload.get("intent"), Some(&json!("read_only")));
        assert_eq!(
            calls[1].payload.get("workspace_root"),
            Some(&json!("/repo"))
        );
    }

    #[derive(Debug, Clone)]
    struct RecordedCall {
        op: String,
        payload: JsonObject,
        timeout_s: u32,
    }

    #[derive(Debug, Default)]
    struct RecordingTransport {
        calls: Mutex<Vec<RecordedCall>>,
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
            self.calls.lock().expect("calls lock").push(RecordedCall {
                op: op.as_wire().to_owned(),
                payload,
                timeout_s,
            });
            Ok(json_object(json!({"success": true})))
        }

        async fn call_dynamic(
            &self,
            _sandbox_id: &SandboxId,
            op: &str,
            payload: JsonObject,
            timeout_s: u32,
        ) -> Result<JsonObject, SandboxApiError> {
            self.calls.lock().expect("calls lock").push(RecordedCall {
                op: op.to_owned(),
                payload,
                timeout_s,
            });
            Ok(json_object(json!({"success": true, "value": "ok"})))
        }
    }

    fn metadata_with(transport: Arc<dyn SandboxTransport>) -> ExecutionMetadata {
        let store = Arc::new(InertStore);
        ExecutionMetadata {
            sandbox_id: Some("sandbox-1".parse().expect("sandbox id")),
            agent_run_id: None,
            agent_name: "tester".to_owned(),
            cwd: "/repo".to_owned(),
            repo_root: "/repo".to_owned(),
            exec_cwd: "/repo".to_owned(),
            request_id: None,
            task_id: None,
            attempt_id: None,
            workflow_id: None,
            tool_use_id: None,
            sandbox_invocation_id: Some("inv-1".parse().expect("invocation id")),
            caller: caller(),
            transport,
            task_store: store.clone(),
            request_store: store,
            skill_registry: Arc::new(SkillRegistry::new()),
            workflow_control: None,
            plan_submission: None,
            background_supervisor: None,
            command_session_supervisor: None,
            isolated_workspace: None,
            notifications: None,
            conversation: Arc::from(Vec::<Message>::new()),
        }
    }

    fn caller() -> SandboxCaller {
        SandboxCaller {
            agent_id: "agent-1".to_owned(),
            run_id: String::new(),
            agent_run_id: String::new(),
            task_id: String::new(),
            request_id: String::new(),
            attempt_id: String::new(),
            workflow_id: String::new(),
            tool_id: None,
        }
    }

    struct InertStore;

    impl Sealed for InertStore {}

    #[async_trait]
    impl TaskStore for InertStore {
        async fn upsert_task(&self, _task: &Task) -> Result<(), StoreError> {
            unreachable!("plugin tools do not touch task state")
        }

        async fn get(&self, _id: &TaskId) -> Result<Option<Task>, StoreError> {
            unreachable!("plugin tools do not touch task state")
        }

        async fn set_task_status(
            &self,
            _id: &TaskId,
            _status: TaskStatus,
            _outcomes: Option<&[ExecutionTaskOutcome]>,
            _terminal_tool_result: Option<&JsonObject>,
        ) -> Result<Task, StoreError> {
            unreachable!("plugin tools do not touch task state")
        }

        async fn set_task_status_if_current(
            &self,
            _id: &TaskId,
            _expected: TaskStatus,
            _status: TaskStatus,
            _outcomes: Option<&[ExecutionTaskOutcome]>,
            _terminal_tool_result: Option<&JsonObject>,
        ) -> Result<Option<Task>, StoreError> {
            unreachable!("plugin tools do not touch task state")
        }
    }

    #[async_trait]
    impl RequestStore for InertStore {
        async fn create_request(
            &self,
            _request_id: &RequestId,
            _cwd: &str,
            _sandbox_id: Option<&SandboxId>,
            _request_prompt: &str,
        ) -> Result<(), StoreError> {
            unreachable!("plugin tools do not touch request state")
        }

        async fn get(&self, _id: &RequestId) -> Result<Option<Request>, StoreError> {
            unreachable!("plugin tools do not touch request state")
        }

        async fn set_root_task_id(
            &self,
            _id: &RequestId,
            _root_task_id: &TaskId,
        ) -> Result<Request, StoreError> {
            unreachable!("plugin tools do not touch request state")
        }

        async fn finish_request(
            &self,
            _id: &RequestId,
            _status: &str,
        ) -> Result<Option<Request>, StoreError> {
            unreachable!("plugin tools do not touch request state")
        }
    }
}
