//! Daemon plugin API surface.
//!
//! This module owns the daemon-side `api.plugin.*` routes. The current slice
//! registers and validates plugin/service contracts and status; process-backed
//! PPC and namespace refresh attach behind this boundary.

use std::collections::BTreeMap;
use std::sync::{Mutex, MutexGuard, OnceLock};

use eos_plugin::{
    public_op_name, PluginError, PluginManifest, PluginServiceKey, PluginServiceState,
    PluginServiceStatus, RefreshStrategy, ServiceMode,
};
use eos_protocol::Intent;
use serde_json::{json, Value};

use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;

#[derive(Debug, Clone)]
struct LoadedPluginRuntime {
    digest: String,
    registered_ops: Vec<String>,
    operation_routes: BTreeMap<String, PluginOperationRoute>,
    services: Vec<PluginServiceStatus>,
    runtime_loaded: bool,
}

#[derive(Debug, Clone)]
struct PluginOperationRoute {
    plugin_id: String,
    op_name: String,
    public_op: String,
    intent: Intent,
    auto_workspace_overlay: bool,
    service_id: Option<String>,
    timeout_ms: Option<u64>,
}

impl PluginOperationRoute {
    fn dispatch_mode(&self) -> &'static str {
        match self.intent {
            Intent::ReadOnly => "read_only_service",
            Intent::WriteAllowed if self.auto_workspace_overlay => "write_allowed_oneshot_overlay",
            Intent::WriteAllowed => "self_managed_callback",
            Intent::Lifecycle => "invalid_lifecycle",
        }
    }

    fn to_json(&self) -> Value {
        json!({
            "plugin": self.plugin_id,
            "op_name": self.op_name,
            "public_op": self.public_op,
            "intent": self.intent,
            "auto_workspace_overlay": self.auto_workspace_overlay,
            "service_id": self.service_id,
            "timeout_ms": self.timeout_ms,
            "dispatch_mode": self.dispatch_mode(),
        })
    }
}

#[derive(Debug, Default)]
struct DaemonPluginState {
    loaded: BTreeMap<String, LoadedPluginRuntime>,
}

fn state_cell() -> &'static Mutex<DaemonPluginState> {
    static STATE: OnceLock<Mutex<DaemonPluginState>> = OnceLock::new();
    STATE.get_or_init(|| Mutex::new(DaemonPluginState::default()))
}

fn lock_state() -> Result<MutexGuard<'static, DaemonPluginState>, DaemonError> {
    state_cell()
        .lock()
        .map_err(|_| DaemonError::StateLockPoisoned("plugin registry"))
}

pub(crate) fn op_ensure(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    ensure_plugin_family_allowed(args)?;

    let parsed = ParsedEnsure::from_args(args)?;
    let mut state = lock_state()?;
    let already_loaded = state
        .loaded
        .get(&parsed.plugin_id)
        .is_some_and(|loaded| loaded.digest == parsed.plugin_digest);
    if !already_loaded {
        state.loaded.insert(
            parsed.plugin_id.clone(),
            LoadedPluginRuntime {
                digest: parsed.plugin_digest.clone(),
                registered_ops: parsed.registered_ops.clone(),
                operation_routes: parsed.operation_routes.clone(),
                services: parsed.services.clone(),
                runtime_loaded: parsed.runtime_loaded,
            },
        );
    }
    let loaded = state.loaded.get(&parsed.plugin_id).ok_or_else(|| {
        DaemonError::Plugin(PluginError::Ensure(format!(
            "plugin {} was not recorded after ensure",
            parsed.plugin_id
        )))
    })?;

    Ok(json!({
        "success": true,
        "plugin": parsed.plugin_id,
        "digest": loaded.digest,
        "registered_ops": loaded.registered_ops,
        "runtime_loaded": loaded.runtime_loaded,
        "runtime_warmed": false,
        "service_processes_started": false,
        "already_loaded": already_loaded,
        "operation_routes": route_values(&loaded.operation_routes),
        "services": loaded.services,
    }))
}

pub(crate) fn op_status(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    ensure_plugin_family_allowed(args)?;
    let state = lock_state()?;
    let loaded_plugins = state
        .loaded
        .iter()
        .map(|(name, loaded)| {
            json!({
                "name": name,
                "digest": loaded.digest,
                "ops": loaded.registered_ops,
                "operation_routes": route_values(&loaded.operation_routes),
                "services": loaded.services,
                "runtime_loaded": loaded.runtime_loaded,
            })
        })
        .collect::<Vec<_>>();
    Ok(json!({
        "success": true,
        "loaded_plugins": loaded_plugins,
        "pending": [],
    }))
}

pub(crate) fn dispatch_registered_op(
    op: &str,
    args: &Value,
    _context: DispatchContext<'_>,
) -> Option<Result<Value, DaemonError>> {
    if !op.starts_with("plugin.") {
        return None;
    }
    let route = match route_for_op(op) {
        Ok(Some(route)) => route,
        Ok(None) => return None,
        Err(err) => return Some(Err(err)),
    };
    Some(dispatch_deferred_route(route, args))
}

#[cfg(test)]
pub(crate) fn reset_for_tests() {
    if let Ok(mut state) = state_cell().lock() {
        state.loaded.clear();
    }
}

fn ensure_plugin_family_allowed(args: &Value) -> Result<(), DaemonError> {
    let agent_id = args
        .get("agent_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim();
    if !agent_id.is_empty() && crate::isolated::agent_has_active_handle(agent_id) {
        return Err(DaemonError::Plugin(
            PluginError::ForbiddenInIsolatedWorkspace,
        ));
    }
    Ok(())
}

struct ParsedEnsure {
    plugin_id: String,
    plugin_digest: String,
    registered_ops: Vec<String>,
    operation_routes: BTreeMap<String, PluginOperationRoute>,
    services: Vec<PluginServiceStatus>,
    runtime_loaded: bool,
}

impl ParsedEnsure {
    fn from_args(args: &Value) -> Result<Self, DaemonError> {
        if let Some(manifest_value) = args.get("manifest") {
            let manifest: PluginManifest = serde_json::from_value(manifest_value.clone())
                .map_err(|err| PluginError::Manifest(err.to_string()))?;
            manifest.validate()?;
            return Self::from_manifest(args, manifest);
        }

        let plugin_id = args
            .get("plugin")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_owned();
        validate_public_identifier("plugin", &plugin_id)?;
        let plugin_digest = args
            .get("digest")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_owned();
        Ok(Self {
            plugin_id,
            plugin_digest,
            registered_ops: Vec::new(),
            operation_routes: BTreeMap::new(),
            services: Vec::new(),
            runtime_loaded: false,
        })
    }

    fn from_manifest(args: &Value, manifest: PluginManifest) -> Result<Self, DaemonError> {
        let operation_routes = manifest
            .operations
            .iter()
            .map(|op| {
                let public_op = public_op_name(&manifest.plugin_id, &op.op_name);
                (
                    public_op.clone(),
                    PluginOperationRoute {
                        plugin_id: manifest.plugin_id.clone(),
                        op_name: op.op_name.clone(),
                        public_op,
                        intent: op.intent,
                        auto_workspace_overlay: op.auto_workspace_overlay,
                        service_id: op.service_id.clone(),
                        timeout_ms: op.timeout_ms,
                    },
                )
            })
            .collect::<BTreeMap<_, _>>();
        let registered_ops = operation_routes.keys().cloned().collect::<Vec<_>>();
        let services = if manifest.services.is_empty() {
            Vec::new()
        } else {
            let layer_stack_root = require_string(args, "layer_stack_root")?;
            let workspace_root = require_string(args, "workspace_root")?;
            manifest
                .services
                .iter()
                .map(|service| {
                    let key = PluginServiceKey::new(
                        layer_stack_root.clone(),
                        workspace_root.clone(),
                        manifest.plugin_id.clone(),
                        manifest.plugin_digest.clone(),
                        service.service_id.clone(),
                        service.service_profile_digest.clone(),
                        service.service_mode,
                        service.refresh_strategy,
                    )?;
                    let mut status = PluginServiceStatus::new(key);
                    status.state = PluginServiceState::Stopped;
                    status.registered_ops = registered_ops.clone();
                    status.last_error = Some(
                        "process-backed PPC execution is not started in this slice".to_owned(),
                    );
                    Ok(status)
                })
                .collect::<Result<Vec<_>, PluginError>>()?
        };
        Ok(Self {
            plugin_id: manifest.plugin_id,
            plugin_digest: manifest.plugin_digest,
            registered_ops,
            operation_routes,
            services,
            runtime_loaded: true,
        })
    }
}

fn require_string(args: &Value, key: &str) -> Result<String, DaemonError> {
    let value = args
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_owned();
    if value.is_empty() {
        return Err(DaemonError::Plugin(PluginError::Ensure(format!(
            "api.plugin.ensure requires {key}"
        ))));
    }
    Ok(value)
}

fn validate_public_identifier(field: &str, value: &str) -> Result<(), DaemonError> {
    if value.is_empty() {
        return Err(DaemonError::Plugin(PluginError::Ensure(format!(
            "api.plugin.ensure requires {field} name"
        ))));
    }
    let mut chars = value.chars();
    match chars.next() {
        Some(c) if c == '_' || c.is_ascii_alphabetic() => {}
        _ => {
            return Err(DaemonError::Plugin(PluginError::Ensure(format!(
                "{field} must start with an ASCII letter or underscore"
            ))));
        }
    }
    if chars.all(|c| c == '_' || c.is_ascii_alphanumeric()) {
        Ok(())
    } else {
        Err(DaemonError::Plugin(PluginError::Ensure(format!(
            "{field} contains unsupported characters"
        ))))
    }
}

fn route_values(routes: &BTreeMap<String, PluginOperationRoute>) -> Vec<Value> {
    routes.values().map(PluginOperationRoute::to_json).collect()
}

fn route_for_op(op: &str) -> Result<Option<PluginOperationRoute>, DaemonError> {
    let state = lock_state()?;
    Ok(state
        .loaded
        .values()
        .find_map(|loaded| loaded.operation_routes.get(op).cloned()))
}

fn dispatch_deferred_route(
    route: PluginOperationRoute,
    args: &Value,
) -> Result<Value, DaemonError> {
    ensure_plugin_family_allowed(args)?;
    Ok(json!({
        "success": false,
        "status": "deferred",
        "op": route.public_op,
        "plugin": route.plugin_id,
        "op_name": route.op_name,
        "intent": route.intent,
        "auto_workspace_overlay": route.auto_workspace_overlay,
        "service_id": route.service_id,
        "dispatch_mode": route.dispatch_mode(),
        "error": {
            "kind": "plugin_dispatch_deferred",
            "message": "process-backed PPC execution is not implemented yet",
            "details": {
                "op": route.public_op,
                "dispatch_mode": route.dispatch_mode(),
            },
        },
    }))
}

#[allow(dead_code)]
fn _keeps_strategy_names_linked(_: RefreshStrategy, _: ServiceMode) {}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dispatcher::OpTable;
    use eos_protocol::Request;
    use std::sync::Mutex;

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    fn lsp_manifest(digest: &str, op_name: &str) -> Value {
        json!({
            "plugin_id": "lsp",
            "plugin_version": "0.1.0",
            "plugin_digest": digest,
            "services": [{
                "service_id": "pyright",
                "service_profile_digest": format!("profile-{digest}"),
                "service_mode": "workspace_snapshot_refresh",
                "refresh_strategy": "remount_workspace_and_notify",
                "command": ["pyright-langserver", "--stdio"],
                "ppc_protocol_version": 1
            }],
            "operations": [{
                "op_name": op_name,
                "intent": "read_only",
                "service_id": "pyright"
            }]
        })
    }

    #[test]
    fn ensure_records_manifest_services_and_status_lists_them() {
        let _guard = TEST_LOCK.lock().expect("plugin test lock poisoned");
        reset_for_tests();
        let response = op_ensure(
            &json!({
                "manifest": lsp_manifest("digest-a", "hover"),
                "layer_stack_root": "/eos/plugin/layer-stack",
                "workspace_root": "/eos/plugin/workspace"
            }),
            DispatchContext::empty(),
        )
        .expect("ensure response");
        assert_eq!(response["success"], true);
        assert_eq!(response["registered_ops"], json!(["plugin.lsp.hover"]));
        assert_eq!(
            response["operation_routes"][0]["dispatch_mode"],
            "read_only_service"
        );
        assert_eq!(response["services"][0]["state"], "stopped");

        let status = op_status(&json!({}), DispatchContext::empty()).expect("status response");
        assert_eq!(status["loaded_plugins"][0]["name"], "lsp");
        reset_for_tests();
    }

    #[test]
    fn ensure_is_idempotent_for_same_digest() {
        let _guard = TEST_LOCK.lock().expect("plugin test lock poisoned");
        reset_for_tests();
        let first = op_ensure(
            &json!({"plugin": "demo", "digest": "a"}),
            DispatchContext::empty(),
        )
        .expect("first ensure");
        let second = op_ensure(
            &json!({"plugin": "demo", "digest": "a"}),
            DispatchContext::empty(),
        )
        .expect("second ensure");
        assert_eq!(first["already_loaded"], false);
        assert_eq!(second["already_loaded"], true);
        reset_for_tests();
    }

    #[test]
    fn op_table_registers_plugin_status_and_ensure() {
        let _guard = TEST_LOCK.lock().expect("plugin test lock poisoned");
        reset_for_tests();
        let table = OpTable::with_builtins();
        let ensure = table.dispatch(&Request {
            op: "api.plugin.ensure".to_owned(),
            invocation_id: "plugin-ensure-test".to_owned(),
            args: json!({"plugin": "demo", "digest": "a"}),
        });
        assert_eq!(ensure["success"], true);

        let status = table.dispatch(&Request {
            op: "api.plugin.status".to_owned(),
            invocation_id: "plugin-status-test".to_owned(),
            args: json!({}),
        });
        assert_eq!(status["success"], true);
        let loaded = status["loaded_plugins"].as_array().expect("loaded_plugins");
        assert!(loaded.iter().any(|plugin| plugin["name"] == "demo"));
        reset_for_tests();
    }

    #[test]
    fn registered_plugin_op_routes_to_deferred_dispatch_not_unknown_op() {
        let _guard = TEST_LOCK.lock().expect("plugin test lock poisoned");
        reset_for_tests();
        let table = OpTable::with_builtins();
        let ensure = table.dispatch(&Request {
            op: "api.plugin.ensure".to_owned(),
            invocation_id: "plugin-ensure-test".to_owned(),
            args: json!({
                "manifest": lsp_manifest("digest-a", "hover"),
                "layer_stack_root": "/eos/plugin/layer-stack",
                "workspace_root": "/eos/plugin/workspace"
            }),
        });
        assert_eq!(ensure["success"], true);

        let routed = table.dispatch(&Request {
            op: "plugin.lsp.hover".to_owned(),
            invocation_id: "plugin-hover-test".to_owned(),
            args: json!({"agent_id": "agent-plugin"}),
        });
        assert_eq!(routed["success"], false);
        assert_eq!(routed["status"], "deferred");
        assert_eq!(routed["error"]["kind"], "plugin_dispatch_deferred");
        assert_eq!(routed["dispatch_mode"], "read_only_service");

        let missing = table.dispatch(&Request {
            op: "plugin.lsp.missing".to_owned(),
            invocation_id: "plugin-missing-test".to_owned(),
            args: json!({}),
        });
        assert_eq!(missing["error"]["kind"], "unknown_op");
        reset_for_tests();
    }

    #[test]
    fn digest_reload_replaces_dynamic_plugin_routes() {
        let _guard = TEST_LOCK.lock().expect("plugin test lock poisoned");
        reset_for_tests();
        let table = OpTable::with_builtins();
        let first = table.dispatch(&Request {
            op: "api.plugin.ensure".to_owned(),
            invocation_id: "plugin-ensure-a".to_owned(),
            args: json!({
                "manifest": lsp_manifest("digest-a", "hover"),
                "layer_stack_root": "/eos/plugin/layer-stack",
                "workspace_root": "/eos/plugin/workspace"
            }),
        });
        assert_eq!(first["registered_ops"], json!(["plugin.lsp.hover"]));

        let second = table.dispatch(&Request {
            op: "api.plugin.ensure".to_owned(),
            invocation_id: "plugin-ensure-b".to_owned(),
            args: json!({
                "manifest": lsp_manifest("digest-b", "diagnostics"),
                "layer_stack_root": "/eos/plugin/layer-stack",
                "workspace_root": "/eos/plugin/workspace"
            }),
        });
        assert_eq!(second["registered_ops"], json!(["plugin.lsp.diagnostics"]));

        let old = table.dispatch(&Request {
            op: "plugin.lsp.hover".to_owned(),
            invocation_id: "plugin-hover-old".to_owned(),
            args: json!({}),
        });
        assert_eq!(old["error"]["kind"], "unknown_op");

        let current = table.dispatch(&Request {
            op: "plugin.lsp.diagnostics".to_owned(),
            invocation_id: "plugin-diagnostics-current".to_owned(),
            args: json!({}),
        });
        assert_eq!(current["error"]["kind"], "plugin_dispatch_deferred");
        reset_for_tests();
    }
}
