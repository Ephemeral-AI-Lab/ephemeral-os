#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use eos_sandbox_port::{DaemonOp, SandboxPortError, SandboxTransport};
use eos_types::{JsonObject, SandboxId};
use serde_json::{json, Value};

use crate::support::metadata;
use crate::tools::{CallerScope, SandboxToolService, SkillToolService};
use eos_tool_ports::{IsolatedWorkspaceToolService, ToolName, ToolRegistry};

#[derive(Debug, Clone)]
struct Call {
    op: DaemonOp,
    payload: JsonObject,
}

#[derive(Debug)]
struct IsolatedWorkspaceTestTransport {
    calls: Mutex<Vec<Call>>,
    response: JsonObject,
    error: Option<SandboxPortError>,
}

impl IsolatedWorkspaceTestTransport {
    fn ok(response: Value) -> Arc<Self> {
        Arc::new(Self {
            calls: Mutex::new(Vec::new()),
            response: object(response),
            error: None,
        })
    }

    fn err(error: SandboxPortError) -> Arc<Self> {
        Arc::new(Self {
            calls: Mutex::new(Vec::new()),
            response: JsonObject::new(),
            error: Some(error),
        })
    }

    fn calls(&self) -> Vec<Call> {
        self.calls.lock().unwrap().clone()
    }
}

#[async_trait]
impl SandboxTransport for IsolatedWorkspaceTestTransport {
    async fn call(
        &self,
        _sandbox_id: &SandboxId,
        op: DaemonOp,
        payload: JsonObject,
        _timeout_s: u32,
    ) -> Result<JsonObject, SandboxPortError> {
        self.calls.lock().unwrap().push(Call { op, payload });
        if let Some(error) = &self.error {
            return Err(error.clone());
        }
        Ok(self.response.clone())
    }
}

fn object(value: Value) -> JsonObject {
    match value {
        Value::Object(map) => map,
        _ => JsonObject::new(),
    }
}

fn obj(pairs: &[(&str, Value)]) -> JsonObject {
    pairs
        .iter()
        .map(|(key, value)| ((*key).to_owned(), value.clone()))
        .collect()
}

fn registry(transport: Arc<dyn SandboxTransport>) -> ToolRegistry {
    registry_with_sandbox_service(SandboxToolService::new(transport))
}

fn registry_with_sandbox_service(sandbox_service: SandboxToolService) -> ToolRegistry {
    crate::tools::build_default_registry_with_services(
        &crate::tools::repo_tools_config(),
        &CallerScope::default(),
        sandbox_service,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        SkillToolService::new(Arc::new(eos_skills::SkillRegistry::new())),
    )
}

fn registry_with_state_updates(
    transport: Arc<dyn SandboxTransport>,
    updates: Arc<Mutex<Vec<bool>>>,
) -> ToolRegistry {
    let state_service = IsolatedWorkspaceToolService::new(move |_agent_run_id, is_isolated| {
        let updates = updates.clone();
        async move {
            updates.lock().unwrap().push(is_isolated);
            Ok(())
        }
    });
    registry_with_sandbox_service(
        SandboxToolService::new(transport).with_isolated_workspace_service(state_service),
    )
}

fn ctx() -> eos_tool_ports::ExecutionMetadata {
    let mut ctx = metadata();
    ctx.sandbox_id = Some("sb-1".parse().unwrap());
    ctx
}

async fn execute(
    registry: &ToolRegistry,
    name: ToolName,
    input: JsonObject,
) -> eos_tool_ports::ToolResult {
    registry
        .get(name)
        .expect("registered")
        .executor()
        .execute(&input, &ctx())
        .await
        .expect("tool execution")
}

#[tokio::test]
async fn enter_isolated_workspace_marks_agent_isolated_on_success() {
    let transport = IsolatedWorkspaceTestTransport::ok(json!({"success": true}));
    let updates = Arc::new(Mutex::new(Vec::new()));
    let registry = registry_with_state_updates(transport, updates.clone());

    let res = execute(
        &registry,
        ToolName::EnterIsolatedWorkspace,
        JsonObject::new(),
    )
    .await;

    assert!(!res.is_error, "{res:?}");
    assert_eq!(*updates.lock().unwrap(), vec![true]);
}

#[tokio::test]
async fn enter_isolated_workspace_uses_default_layer_stack_root() {
    let transport = IsolatedWorkspaceTestTransport::ok(json!({"success": true}));
    let registry = registry(transport.clone());

    let res = execute(
        &registry,
        ToolName::EnterIsolatedWorkspace,
        JsonObject::new(),
    )
    .await;

    assert!(!res.is_error, "{res:?}");
    let calls = transport.calls();
    assert_eq!(calls[0].op, DaemonOp::IsolatedWorkspaceEnter);
    assert_eq!(
        calls[0].payload["layer_stack_root"],
        json!("/eos/state/layer-stack")
    );
}

#[tokio::test]
async fn enter_isolated_workspace_forwards_explicit_layer_stack_root() {
    let transport = IsolatedWorkspaceTestTransport::ok(json!({"success": true}));
    let registry = registry(transport.clone());

    let res = execute(
        &registry,
        ToolName::EnterIsolatedWorkspace,
        obj(&[("layer_stack_root", json!("/custom/layers"))]),
    )
    .await;

    assert!(!res.is_error, "{res:?}");
    let calls = transport.calls();
    assert_eq!(calls[0].op, DaemonOp::IsolatedWorkspaceEnter);
    assert_eq!(
        calls[0].payload["layer_stack_root"],
        json!("/custom/layers")
    );
}

#[tokio::test]
async fn enter_isolated_workspace_renders_success_payload() {
    let transport = IsolatedWorkspaceTestTransport::ok(json!({
        "success": true,
        "manifest_version": "v2",
        "manifest_root_hash": "hash-123",
    }));
    let registry = registry(transport);

    let res = execute(
        &registry,
        ToolName::EnterIsolatedWorkspace,
        JsonObject::new(),
    )
    .await;

    assert!(!res.is_error, "{res:?}");
    let payload: Value = serde_json::from_str(&res.output).unwrap();
    assert_eq!(payload["success"], json!(true));
    assert_eq!(payload["manifest_version"], json!("v2"));
    assert_eq!(payload["manifest_root_hash"], json!("hash-123"));
    assert!(payload["error"].is_null());
}

#[tokio::test]
async fn enter_isolated_workspace_renders_api_failure_as_tool_error() {
    let transport = IsolatedWorkspaceTestTransport::err(SandboxPortError::transport(
        Some("already_active".to_owned()),
        "isolated workspace already active",
    ));
    let updates = Arc::new(Mutex::new(Vec::new()));
    let registry = registry_with_state_updates(transport, updates.clone());

    let res = execute(
        &registry,
        ToolName::EnterIsolatedWorkspace,
        JsonObject::new(),
    )
    .await;

    assert!(res.is_error);
    let payload: Value = serde_json::from_str(&res.output).unwrap();
    assert_eq!(payload["success"], json!(false));
    assert_eq!(payload["error"]["kind"], json!("already_active"));
    assert_eq!(
        payload["error"]["message"],
        json!("isolated workspace already active")
    );
    assert!(updates.lock().unwrap().is_empty());
}

#[tokio::test]
async fn exit_isolated_workspace_clears_agent_isolated_on_success() {
    let transport = IsolatedWorkspaceTestTransport::ok(json!({"success": true}));
    let updates = Arc::new(Mutex::new(Vec::new()));
    let registry = registry_with_state_updates(transport, updates.clone());

    let res = execute(
        &registry,
        ToolName::ExitIsolatedWorkspace,
        JsonObject::new(),
    )
    .await;

    assert!(!res.is_error, "{res:?}");
    assert_eq!(*updates.lock().unwrap(), vec![false]);
}

#[tokio::test]
async fn exit_isolated_workspace_uses_default_grace() {
    let transport = IsolatedWorkspaceTestTransport::ok(json!({"success": true}));
    let registry = registry(transport.clone());

    let res = execute(
        &registry,
        ToolName::ExitIsolatedWorkspace,
        JsonObject::new(),
    )
    .await;

    assert!(!res.is_error, "{res:?}");
    let calls = transport.calls();
    assert_eq!(calls[0].op, DaemonOp::IsolatedWorkspaceExit);
    assert_eq!(calls[0].payload["grace_s"], json!(5.0));
}

#[tokio::test]
async fn exit_isolated_workspace_forwards_explicit_grace() {
    let transport = IsolatedWorkspaceTestTransport::ok(json!({"success": true}));
    let registry = registry(transport.clone());

    let res = execute(
        &registry,
        ToolName::ExitIsolatedWorkspace,
        obj(&[("grace_s", json!(0.25))]),
    )
    .await;

    assert!(!res.is_error, "{res:?}");
    let calls = transport.calls();
    assert_eq!(calls[0].op, DaemonOp::IsolatedWorkspaceExit);
    assert_eq!(calls[0].payload["grace_s"], json!(0.25));
}

#[tokio::test]
async fn exit_isolated_workspace_renders_success_payload() {
    let transport = IsolatedWorkspaceTestTransport::ok(json!({
        "success": true,
        "evicted_upperdir_bytes": 4096,
        "lifetime_s": 12.5,
        "phases_ms": {"drain": 1.25, "teardown": 2.5},
    }));
    let registry = registry(transport);

    let res = execute(
        &registry,
        ToolName::ExitIsolatedWorkspace,
        JsonObject::new(),
    )
    .await;

    assert!(!res.is_error, "{res:?}");
    let payload: Value = serde_json::from_str(&res.output).unwrap();
    assert_eq!(payload["success"], json!(true));
    assert_eq!(payload["evicted_upperdir_bytes"], json!(4096));
    assert_eq!(payload["lifetime_s"], json!(12.5));
    assert_eq!(payload["phases_ms"]["drain"], json!(1.25));
    assert!(payload["error"].is_null());
}

#[tokio::test]
async fn exit_isolated_workspace_renders_api_failure_as_tool_error() {
    let transport =
        IsolatedWorkspaceTestTransport::err(SandboxPortError::decode("bad lifecycle payload"));
    let updates = Arc::new(Mutex::new(Vec::new()));
    let registry = registry_with_state_updates(transport, updates.clone());

    let res = execute(
        &registry,
        ToolName::ExitIsolatedWorkspace,
        JsonObject::new(),
    )
    .await;

    assert!(res.is_error);
    let payload: Value = serde_json::from_str(&res.output).unwrap();
    assert_eq!(payload["success"], json!(false));
    assert_eq!(payload["error"]["kind"], json!("decode_error"));
    assert_eq!(payload["error"]["message"], json!("bad lifecycle payload"));
    assert!(updates.lock().unwrap().is_empty());
}
