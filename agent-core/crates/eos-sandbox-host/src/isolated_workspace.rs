//! Host-side isolated-workspace enter/exit: background-task gating on enter,
//! background cancel/drain on exit, and the daemon enter/exit RPCs. Faithful
//! port of `sandbox/host/isolated_workspace_lifecycle.py` (DAEMON branch only —
//! the Python LOCAL `_control_plane` branch is daemon-side and not ported, so a
//! non-empty `SandboxId` is required).
//!
//! Everything deep (namespace, `LayerStack`, snapshot lease, scratch teardown)
//! stays daemon-side: the host only issues `api.isolated_workspace.{enter,exit}`
//! and maps the response. The Python `lifecycle_operation` audit span is reduced
//! to nothing here (this crate has no `eos-audit` dependency); only the daemon
//! `phases_ms` + the local evicted count flow into the result timings.

use std::collections::BTreeMap;

use async_trait::async_trait;
use eos_sandbox_api::{
    command_session_count, EnterIsolatedWorkspaceRequest, EnterIsolatedWorkspaceResult,
    ExitIsolatedWorkspaceRequest, ExitIsolatedWorkspaceResult, LifecycleError, LifecycleResultBase,
};
use eos_types::{JsonObject, SandboxId};
use serde_json::Value;

use crate::daemon_client::{DaemonClient, DEFAULT_LAYER_STACK_ROOT};
use crate::error::SandboxHostError;

const ISOLATED_OP_TIMEOUT_S: u32 = 180;

/// The host-side seam for the runtime's per-agent background-task manager (the
/// Python duck-typed `background_manager`). `eos-runtime` injects the concrete
/// implementor; `None` means no local background work (counts/cancels as 0).
#[async_trait]
pub trait BackgroundManager: Send + Sync + std::fmt::Debug {
    /// Count this agent's in-flight sandbox-bound background tasks (sync).
    fn count_by_agent(&self, agent_id: &str) -> u64;
    /// Cancel/drain this agent's background tasks within `grace_s`; returns the
    /// number evicted.
    async fn cancel_by_agent(&self, agent_id: &str, grace_s: f64) -> u64;
}

/// Enter an isolated workspace. Never propagates: every failure is returned in
/// the result's `error` field.
pub async fn enter_isolated_workspace(
    request: &EnterIsolatedWorkspaceRequest,
    background_manager: Option<&dyn BackgroundManager>,
    sandbox_id: &SandboxId,
    daemon: &DaemonClient,
) -> EnterIsolatedWorkspaceResult {
    let agent_id = request.base.caller.agent_id.clone();

    // GATE (before any span): reject when local OR daemon work is in flight
    // (MAX, not sum). A failed daemon count check fails closed.
    let local = count_by_agent(background_manager, &agent_id);
    let daemon_count = match command_session_count(daemon, sandbox_id, &agent_id).await {
        Ok(count) => u64::from(count),
        Err(_) => {
            return enter_failure(LifecycleError {
                kind: "command_session_count_unavailable".to_owned(),
                message: "daemon command session count check failed".to_owned(),
                details: BTreeMap::from([("sandbox_id".to_owned(), sandbox_id.to_string())]),
            });
        }
    };
    let in_flight = local.max(daemon_count);
    if in_flight > 0 {
        return enter_failure(LifecycleError {
            kind: "ephemeral_jobs_in_flight".to_owned(),
            message: "sandbox-bound background tasks are still running".to_owned(),
            details: BTreeMap::from([("count".to_owned(), in_flight.to_string())]),
        });
    }

    daemon_enter(daemon, sandbox_id, request).await
}

/// Exit an isolated workspace. Never propagates: failures are returned in the
/// result's `error` field. `grace_s` governs the local background drain only —
/// it is NOT forwarded to the daemon teardown.
pub async fn exit_isolated_workspace(
    request: &ExitIsolatedWorkspaceRequest,
    background_manager: Option<&dyn BackgroundManager>,
    sandbox_id: &SandboxId,
    daemon: &DaemonClient,
) -> ExitIsolatedWorkspaceResult {
    let agent_id = request.base.caller.agent_id.clone();
    // Drain local background work BEFORE teardown (grace_s flows here only).
    let evicted = cancel_by_agent(background_manager, &agent_id, request.grace_s).await;
    daemon_exit(daemon, sandbox_id, &agent_id, evicted).await
}

async fn daemon_enter(
    daemon: &DaemonClient,
    sandbox_id: &SandboxId,
    request: &EnterIsolatedWorkspaceRequest,
) -> EnterIsolatedWorkspaceResult {
    let mut args = JsonObject::new();
    args.insert(
        "agent_id".to_owned(),
        Value::String(request.base.caller.agent_id.clone()),
    );
    args.insert(
        "layer_stack_root".to_owned(),
        Value::String(request.layer_stack_root.clone()),
    );
    match daemon
        .call_daemon_api(
            sandbox_id,
            "api.isolated_workspace.enter",
            args,
            ISOLATED_OP_TIMEOUT_S,
            &request.layer_stack_root,
        )
        .await
    {
        Err(err) => enter_failure(lifecycle_error_from_host(err)),
        Ok(response) => {
            if let Some(error) = response.get("error").filter(|v| !v.is_null()) {
                return enter_failure(lifecycle_error_from_mapping(error));
            }
            EnterIsolatedWorkspaceResult {
                base: LifecycleResultBase {
                    success: response
                        .get("success")
                        .and_then(Value::as_bool)
                        .unwrap_or(true),
                    timings: BTreeMap::new(),
                    error: None,
                },
                manifest_version: response
                    .get("manifest_version")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_owned(),
                manifest_root_hash: response
                    .get("manifest_root_hash")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_owned(),
            }
        }
    }
}

async fn daemon_exit(
    daemon: &DaemonClient,
    sandbox_id: &SandboxId,
    agent_id: &str,
    evicted: u64,
) -> ExitIsolatedWorkspaceResult {
    let mut args = JsonObject::new();
    args.insert("agent_id".to_owned(), Value::String(agent_id.to_owned()));
    match daemon
        .call_daemon_api(
            sandbox_id,
            "api.isolated_workspace.exit",
            args,
            ISOLATED_OP_TIMEOUT_S,
            DEFAULT_LAYER_STACK_ROOT,
        )
        .await
    {
        Err(err) => exit_failure(lifecycle_error_from_host(err)),
        Ok(response) => {
            if let Some(error) = response.get("error").filter(|v| !v.is_null()) {
                return exit_failure(lifecycle_error_from_mapping(error));
            }
            // Daemon phases + the local evicted count; the merged map becomes both
            // `phases_ms` and `timings`.
            let mut phases: BTreeMap<String, f64> = response
                .get("phases_ms")
                .and_then(Value::as_object)
                .map(|m| {
                    m.iter()
                        .filter_map(|(k, v)| v.as_f64().map(|f| (k.clone(), f)))
                        .collect()
                })
                .unwrap_or_default();
            #[allow(clippy::cast_precision_loss)]
            phases.insert("evicted_background_tasks".to_owned(), evicted as f64);
            ExitIsolatedWorkspaceResult {
                base: LifecycleResultBase {
                    success: response
                        .get("success")
                        .and_then(Value::as_bool)
                        .unwrap_or(true),
                    timings: phases.clone(),
                    error: None,
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
    }
}

fn count_by_agent(background_manager: Option<&dyn BackgroundManager>, agent_id: &str) -> u64 {
    background_manager.map_or(0, |manager| manager.count_by_agent(agent_id))
}

async fn cancel_by_agent(
    background_manager: Option<&dyn BackgroundManager>,
    agent_id: &str,
    grace_s: f64,
) -> u64 {
    match background_manager {
        Some(manager) => manager.cancel_by_agent(agent_id, grace_s).await,
        None => 0,
    }
}

fn enter_failure(error: LifecycleError) -> EnterIsolatedWorkspaceResult {
    EnterIsolatedWorkspaceResult {
        base: LifecycleResultBase {
            success: false,
            timings: BTreeMap::new(),
            error: Some(error),
        },
        manifest_version: String::new(),
        manifest_root_hash: String::new(),
    }
}

fn exit_failure(error: LifecycleError) -> ExitIsolatedWorkspaceResult {
    ExitIsolatedWorkspaceResult {
        base: LifecycleResultBase {
            success: false,
            timings: BTreeMap::new(),
            error: Some(error),
        },
        evicted_upperdir_bytes: 0,
        lifetime_s: 0.0,
        phases_ms: BTreeMap::new(),
    }
}

/// Convert any host transport error into a `LifecycleError` (default kind
/// `internal_error`, distinct from the daemon-client `RuntimeError` default —
/// this is the host layer).
fn lifecycle_error_from_host(err: SandboxHostError) -> LifecycleError {
    match err {
        SandboxHostError::DaemonDispatch {
            kind,
            message,
            details,
        } => LifecycleError {
            kind: if kind.is_empty() {
                "internal_error".to_owned()
            } else {
                kind
            },
            message,
            details: details
                .iter()
                .map(|(k, v)| (k.clone(), plain_string(v)))
                .collect(),
        },
        other => LifecycleError {
            kind: "internal_error".to_owned(),
            message: other.to_string(),
            details: BTreeMap::new(),
        },
    }
}

/// Convert a handler-level policy `error` mapping into a `LifecycleError`.
fn lifecycle_error_from_mapping(error: &Value) -> LifecycleError {
    match error {
        Value::Object(map) => LifecycleError {
            kind: map
                .get("kind")
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .unwrap_or("internal_error")
                .to_owned(),
            message: map
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned(),
            details: match map.get("details") {
                Some(Value::Object(details)) => details
                    .iter()
                    .map(|(k, v)| (k.clone(), plain_string(v)))
                    .collect(),
                _ => BTreeMap::new(),
            },
        },
        other => LifecycleError {
            kind: "internal_error".to_owned(),
            message: plain_string(other),
            details: BTreeMap::new(),
        },
    }
}

fn plain_string(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use std::sync::{Arc, Mutex};

    use super::*;
    use crate::provider::RawExecResult;
    use crate::registry::ProviderRegistry;
    use crate::testutil::MockAdapter;

    fn sid() -> SandboxId {
        "sb-1".parse().unwrap()
    }

    fn caller(agent_id: &str) -> eos_sandbox_api::SandboxCaller {
        eos_sandbox_api::SandboxCaller {
            agent_id: agent_id.to_owned(),
            run_id: String::new(),
            agent_run_id: String::new(),
            task_id: String::new(),
            request_id: String::new(),
            attempt_id: String::new(),
            workflow_id: String::new(),
            tool_id: None,
        }
    }

    fn enter_request(agent_id: &str) -> EnterIsolatedWorkspaceRequest {
        EnterIsolatedWorkspaceRequest {
            base: eos_sandbox_api::SandboxRequestBase {
                caller: caller(agent_id),
                description: String::new(),
                invocation_id: None,
            },
            layer_stack_root: "/eos/layer-stack".to_owned(),
        }
    }

    fn exit_request(agent_id: &str, grace_s: f64) -> ExitIsolatedWorkspaceRequest {
        ExitIsolatedWorkspaceRequest {
            base: eos_sandbox_api::SandboxRequestBase {
                caller: caller(agent_id),
                description: String::new(),
                invocation_id: None,
            },
            grace_s,
        }
    }

    #[derive(Debug)]
    struct FakeBg {
        count: u64,
        evicted: u64,
        last_grace: Mutex<f64>,
    }

    #[async_trait]
    impl BackgroundManager for FakeBg {
        fn count_by_agent(&self, _agent_id: &str) -> u64 {
            self.count
        }
        async fn cancel_by_agent(&self, _agent_id: &str, grace_s: f64) -> u64 {
            *self.last_grace.lock().unwrap() = grace_s;
            self.evicted
        }
    }

    fn ok(stdout: &str) -> RawExecResult {
        RawExecResult {
            exit_code: 0,
            stdout: stdout.to_owned(),
            stderr: String::new(),
            success: true,
        }
    }

    fn daemon_with(adapter: MockAdapter) -> DaemonClient {
        let registry = ProviderRegistry::new();
        registry.set_default(Arc::new(adapter));
        DaemonClient::new(Arc::new(registry))
    }

    // AC-11: enter rejects when local background work is in flight (max gate);
    // a successful enter passes the manifest fields through; exit drains with the
    // request grace and folds the evicted count into phases/timings.
    #[tokio::test]
    async fn enter_exit_lifecycle_gates() {
        // (1) gate rejection: local count 5 > daemon count 0 → reject.
        let daemon = daemon_with(MockAdapter::new().with_exec(|cmd| {
            if cmd.contains("command_session_count") {
                ok("{\"count\":0}")
            } else {
                ok("{}")
            }
        }));
        let bg = FakeBg {
            count: 5,
            evicted: 0,
            last_grace: Mutex::new(-1.0),
        };
        let result =
            enter_isolated_workspace(&enter_request("a1"), Some(&bg), &sid(), &daemon).await;
        assert!(!result.base.success);
        let err = result.base.error.unwrap();
        assert_eq!(err.kind, "ephemeral_jobs_in_flight");
        assert_eq!(err.details.get("count").map(String::as_str), Some("5"));

        // (2) successful enter: no in-flight work → daemon enter passes manifest.
        let daemon = daemon_with(MockAdapter::new().with_exec(|cmd| {
            if cmd.contains("api.isolated_workspace.enter") {
                ok("{\"success\":true,\"manifest_version\":\"v1\",\"manifest_root_hash\":\"h1\"}")
            } else if cmd.contains("command_session_count") {
                ok("{\"count\":0}")
            } else {
                ok("{}")
            }
        }));
        let result = enter_isolated_workspace(&enter_request("a1"), None, &sid(), &daemon).await;
        assert!(result.base.success);
        assert_eq!(result.manifest_version, "v1");
        assert_eq!(result.manifest_root_hash, "h1");

        // (3) command-session-count failure → fail-closed with the distinct kind.
        let daemon = daemon_with(MockAdapter::new().with_exec(|cmd| {
            if cmd.contains("command_session_count") {
                ok("{\"error\":{\"kind\":\"boom\",\"message\":\"down\"}}")
            } else {
                ok("{}")
            }
        }));
        let result = enter_isolated_workspace(&enter_request("a1"), None, &sid(), &daemon).await;
        let err = result.base.error.unwrap();
        assert_eq!(err.kind, "command_session_count_unavailable");
        assert_eq!(
            err.details.get("sandbox_id").map(String::as_str),
            Some("sb-1")
        );

        // (4) exit: drain uses request.grace_s; evicted folds into phases/timings.
        let daemon = daemon_with(MockAdapter::new().with_exec(|cmd| {
            if cmd.contains("api.isolated_workspace.exit") {
                ok("{\"success\":true,\"phases_ms\":{\"teardown\":1.5},\"evicted_upperdir_bytes\":100,\"lifetime_s\":9.0}")
            } else {
                ok("{}")
            }
        }));
        let bg = FakeBg {
            count: 0,
            evicted: 3,
            last_grace: Mutex::new(-1.0),
        };
        let result =
            exit_isolated_workspace(&exit_request("a1", 7.5), Some(&bg), &sid(), &daemon).await;
        assert!(result.base.success);
        assert_eq!(result.evicted_upperdir_bytes, 100);
        assert_eq!(result.lifetime_s, 9.0);
        assert_eq!(result.phases_ms.get("teardown"), Some(&1.5));
        assert_eq!(result.phases_ms.get("evicted_background_tasks"), Some(&3.0));
        assert_eq!(
            result.base.timings.get("evicted_background_tasks"),
            Some(&3.0)
        );
        assert_eq!(
            *bg.last_grace.lock().unwrap(),
            7.5,
            "grace flows to the local drain"
        );
    }
}
