//! Pure isolated-workspace helpers: build payload -> call transport -> parse envelope.

use std::collections::BTreeMap;

use eos_types::{JsonObject, SandboxId};
use serde_json::Value;

use crate::error::SandboxPortError;
use crate::models::{
    EnterIsolatedWorkspaceRequest, EnterIsolatedWorkspaceResult, ExitIsolatedWorkspaceRequest,
    ExitIsolatedWorkspaceResult, LifecycleError, LifecycleResultBase,
};
use crate::ops::DaemonOp;
use crate::tool_dispatch::parse::daemon_request_identity_fields;
use crate::transport::SandboxTransport;

const ISOLATED_WORKSPACE_TIMEOUT_S: u32 = 180;

/// Enter one agent's isolated workspace through the sandbox daemon.
pub async fn enter_isolated_workspace(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    request: &EnterIsolatedWorkspaceRequest,
) -> Result<EnterIsolatedWorkspaceResult, SandboxPortError> {
    let mut payload = daemon_request_identity_fields(&request.base);
    payload.insert(
        "layer_stack_root".to_owned(),
        Value::String(request.layer_stack_root.clone()),
    );
    payload.insert(
        "description".to_owned(),
        Value::String(request.base.description_or("enter isolated workspace")),
    );
    let response = transport
        .call(
            sandbox_id,
            DaemonOp::IsolatedWorkspaceEnter,
            payload,
            ISOLATED_WORKSPACE_TIMEOUT_S,
        )
        .await?;
    Ok(parse_enter_result(&response))
}

/// Exit and discard one agent's isolated workspace through the sandbox daemon.
pub async fn exit_isolated_workspace(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    request: &ExitIsolatedWorkspaceRequest,
) -> Result<ExitIsolatedWorkspaceResult, SandboxPortError> {
    let mut payload = daemon_request_identity_fields(&request.base);
    payload.insert(
        "description".to_owned(),
        Value::String(request.base.description_or("exit isolated workspace")),
    );
    payload.insert("grace_s".to_owned(), Value::from(request.grace_s));
    let response = transport
        .call(
            sandbox_id,
            DaemonOp::IsolatedWorkspaceExit,
            payload,
            ISOLATED_WORKSPACE_TIMEOUT_S,
        )
        .await?;
    Ok(parse_exit_result(&response))
}

fn parse_enter_result(response: &JsonObject) -> EnterIsolatedWorkspaceResult {
    EnterIsolatedWorkspaceResult {
        base: lifecycle_base(response),
        manifest_version: string_field(response, "manifest_version"),
        manifest_root_hash: string_field(response, "manifest_root_hash"),
    }
}

fn parse_exit_result(response: &JsonObject) -> ExitIsolatedWorkspaceResult {
    let phases = response
        .get("phases_ms")
        .and_then(Value::as_object)
        .map(f64_map)
        .unwrap_or_default();
    ExitIsolatedWorkspaceResult {
        base: LifecycleResultBase {
            timings: phases.clone(),
            ..lifecycle_base(response)
        },
        evicted_upperdir_bytes: response
            .get("evicted_upperdir_bytes")
            .and_then(Value::as_u64)
            .unwrap_or(0),
        lifetime_s: response
            .get("lifetime_s")
            .and_then(Value::as_f64)
            .unwrap_or(0.0),
        phases_ms: phases,
    }
}

fn lifecycle_base(response: &JsonObject) -> LifecycleResultBase {
    let error = response
        .get("error")
        .filter(|value| !value.is_null())
        .map(lifecycle_error);
    LifecycleResultBase {
        success: response
            .get("success")
            .and_then(Value::as_bool)
            .unwrap_or(error.is_none()),
        timings: response
            .get("timings")
            .and_then(Value::as_object)
            .map(f64_map)
            .unwrap_or_default(),
        error,
    }
}

fn lifecycle_error(value: &Value) -> LifecycleError {
    let Some(map) = value.as_object() else {
        return LifecycleError {
            kind: "lifecycle_error".to_owned(),
            message: value.to_string(),
            details: BTreeMap::new(),
        };
    };
    LifecycleError {
        kind: map
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or("lifecycle_error")
            .to_owned(),
        message: map
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned(),
        details: map
            .get("details")
            .and_then(Value::as_object)
            .map(string_map)
            .unwrap_or_default(),
    }
}

fn string_field(response: &JsonObject, key: &str) -> String {
    response
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_owned()
}

fn f64_map(map: &serde_json::Map<String, Value>) -> BTreeMap<String, f64> {
    map.iter()
        .filter_map(|(key, value)| value.as_f64().map(|number| (key.clone(), number)))
        .collect()
}

fn string_map(map: &serde_json::Map<String, Value>) -> BTreeMap<String, String> {
    map.iter()
        .filter_map(|(key, value)| value.as_str().map(|text| (key.clone(), text.to_owned())))
        .collect()
}

#[cfg(test)]
mod tests {
    use std::sync::Mutex;

    use async_trait::async_trait;
    use eos_types::SandboxId;
    use serde_json::json;

    use super::*;
    use crate::SandboxRequestBase;

    #[derive(Default)]
    struct IsolatedToolApiTestTransport {
        calls: Mutex<Vec<(DaemonOp, JsonObject, u32)>>,
    }

    #[async_trait]
    impl SandboxTransport for IsolatedToolApiTestTransport {
        async fn call(
            &self,
            _sandbox_id: &SandboxId,
            op: DaemonOp,
            payload: JsonObject,
            timeout_s: u32,
        ) -> Result<JsonObject, SandboxPortError> {
            self.calls
                .lock()
                .expect("calls lock")
                .push((op, payload, timeout_s));
            Ok(match op {
                DaemonOp::IsolatedWorkspaceEnter => object(json!({
                    "success": true,
                    "manifest_version": "v1",
                    "manifest_root_hash": "h1"
                })),
                DaemonOp::IsolatedWorkspaceExit => object(json!({
                    "success": true,
                    "evicted_upperdir_bytes": 12,
                    "lifetime_s": 3.5,
                    "phases_ms": {"teardown": 1.25}
                })),
                _ => object(json!({})),
            })
        }
    }

    #[tokio::test]
    async fn enter_builds_daemon_payload_and_parses_result() {
        let transport = IsolatedToolApiTestTransport::default();
        let sandbox_id: SandboxId = "sb-1".parse().expect("sandbox id");
        let result = enter_isolated_workspace(
            &transport,
            &sandbox_id,
            &EnterIsolatedWorkspaceRequest {
                base: base(),
                layer_stack_root: "/eos/layer-stack".to_owned(),
            },
        )
        .await
        .expect("enter");

        assert!(result.base.success);
        assert_eq!(result.manifest_root_hash, "h1");
        let calls = transport.calls.lock().expect("calls lock");
        assert_eq!(calls[0].0, DaemonOp::IsolatedWorkspaceEnter);
        assert_eq!(calls[0].1["caller_id"], json!("agent-1"));
        assert_eq!(calls[0].1["layer_stack_root"], json!("/eos/layer-stack"));
        assert_eq!(calls[0].2, ISOLATED_WORKSPACE_TIMEOUT_S);
    }

    #[tokio::test]
    async fn exit_builds_daemon_payload_and_parses_result() {
        let transport = IsolatedToolApiTestTransport::default();
        let sandbox_id: SandboxId = "sb-1".parse().expect("sandbox id");
        let result = exit_isolated_workspace(
            &transport,
            &sandbox_id,
            &ExitIsolatedWorkspaceRequest {
                base: base(),
                grace_s: 0.25,
            },
        )
        .await
        .expect("exit");

        assert!(result.base.success);
        assert_eq!(result.evicted_upperdir_bytes, 12);
        assert_eq!(result.phases_ms["teardown"], 1.25);
        let calls = transport.calls.lock().expect("calls lock");
        assert_eq!(calls[0].0, DaemonOp::IsolatedWorkspaceExit);
        assert_eq!(calls[0].1["caller_id"], json!("agent-1"));
        assert_eq!(calls[0].1["grace_s"], json!(0.25));
    }

    #[test]
    fn lifecycle_error_defaults_to_failure() {
        let result = parse_enter_result(&object(json!({
            "error": {
                "kind": "already_active",
                "message": "isolated workspace already active",
                "details": {"caller_id": "agent-1"}
            }
        })));

        assert!(!result.base.success);
        assert_eq!(
            result.base.error.expect("lifecycle error").kind,
            "already_active"
        );
    }

    fn base() -> SandboxRequestBase {
        SandboxRequestBase {
            caller_id: "agent-1".to_owned(),
            description: String::new(),
            invocation_id: None,
        }
    }

    fn object(value: Value) -> JsonObject {
        match value {
            Value::Object(map) => map,
            _ => JsonObject::new(),
        }
    }
}
