//! Daemon plugin API surface.
//!
//! This module owns the daemon-side `api.plugin.*` routes. The current slice
//! registers and validates plugin/service contracts and status; process-backed
//! PPC and namespace refresh attach behind this boundary.

mod ppc_router;
mod process;

use std::collections::BTreeMap;
use std::sync::{Mutex, MutexGuard, OnceLock};
use std::time::Duration;

use eos_plugin::{
    public_op_name, PluginError, PluginManifest, PluginServiceKey, PluginServiceManifest,
    PluginServiceState, PluginServiceStatus, PpcDirection, PpcEnvelope, RefreshStrategy,
    ServiceMode,
};
use eos_protocol::Intent;
use serde_json::{json, Value};

use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;
use process::PluginProcessSpec;

#[derive(Debug, Clone)]
struct LoadedPluginRuntime {
    digest: String,
    registered_ops: Vec<String>,
    operation_routes: BTreeMap<String, PluginOperationRoute>,
    services: Vec<PluginServiceStatus>,
    service_processes: Vec<PluginProcessSpec>,
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
    service_instance_id: Option<String>,
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
            "service_instance_id": self.service_instance_id,
            "timeout_ms": self.timeout_ms,
            "dispatch_mode": self.dispatch_mode(),
        })
    }
}

#[derive(Debug, Default)]
struct DaemonPluginState {
    loaded: BTreeMap<String, LoadedPluginRuntime>,
    service_ppc_clients: BTreeMap<String, ppc_router::PpcClient>,
    service_processes: BTreeMap<String, process::PluginServiceProcess>,
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
    let start_services = args
        .get("start_services")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let mut state = lock_state()?;
    let already_loaded = state
        .loaded
        .get(&parsed.plugin_id)
        .is_some_and(|loaded| loaded.digest == parsed.plugin_digest);
    if !already_loaded {
        stop_plugin_service_processes(&mut state, &parsed.plugin_id);
        state.loaded.insert(
            parsed.plugin_id.clone(),
            LoadedPluginRuntime {
                digest: parsed.plugin_digest.clone(),
                registered_ops: parsed.registered_ops.clone(),
                operation_routes: parsed.operation_routes.clone(),
                services: parsed.services.clone(),
                service_processes: parsed.service_processes.clone(),
                runtime_loaded: parsed.runtime_loaded,
            },
        );
    }
    let process_specs = state
        .loaded
        .get(&parsed.plugin_id)
        .ok_or_else(|| {
            DaemonError::Plugin(PluginError::Ensure(format!(
                "plugin {} was not recorded after ensure",
                parsed.plugin_id
            )))
        })?
        .service_processes
        .clone();
    let started_count = if start_services {
        start_service_processes(&mut state, &process_specs)?
    } else {
        0
    };
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
        "service_processes_started": started_count > 0,
        "started_service_process_count": started_count,
        "already_loaded": already_loaded,
        "operation_routes": route_values(&loaded.operation_routes),
        "services": loaded.services,
        "service_processes": process_values(&loaded.service_processes),
        "running_service_processes": running_process_values(&mut state),
        "connected_ppc_routes": connected_ppc_routes(&state),
        "connected_ppc_services": connected_ppc_services(&state),
    }))
}

pub(crate) fn op_status(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    ensure_plugin_family_allowed(args)?;
    let mut state = lock_state()?;
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
                "service_processes": process_values(&loaded.service_processes),
                "runtime_loaded": loaded.runtime_loaded,
            })
        })
        .collect::<Vec<_>>();
    Ok(json!({
        "success": true,
        "loaded_plugins": loaded_plugins,
        "running_service_processes": running_process_values(&mut state),
        "connected_ppc_routes": connected_ppc_routes(&state),
        "connected_ppc_services": connected_ppc_services(&state),
        "pending": [],
    }))
}

pub(crate) fn dispatch_registered_op(
    op: &str,
    invocation_id: &str,
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
    Some(dispatch_registered_route(route, invocation_id, args))
}

#[cfg(test)]
pub(crate) fn reset_for_tests() {
    if let Ok(mut state) = state_cell().lock() {
        state.loaded.clear();
        state.service_ppc_clients.clear();
        state.service_processes.clear();
    }
}

#[cfg(test)]
fn register_ppc_client_for_tests(op: &str, stream: std::os::unix::net::UnixStream) {
    let mut state = state_cell().lock().expect("plugin registry lock");
    let service_instance_id = state
        .loaded
        .values()
        .find_map(|loaded| loaded.operation_routes.get(op))
        .and_then(|route| route.service_instance_id.clone())
        .unwrap_or_else(|| op.to_owned());
    state
        .service_ppc_clients
        .insert(service_instance_id, ppc_router::PpcClient { stream });
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
    service_processes: Vec<PluginProcessSpec>,
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
            service_processes: Vec::new(),
            runtime_loaded: false,
        })
    }

    fn from_manifest(args: &Value, manifest: PluginManifest) -> Result<Self, DaemonError> {
        let ppc_socket_root = ppc_socket_root(args);
        let service_keys = service_keys_for_manifest(args, &manifest)?;
        let operation_routes = manifest
            .operations
            .iter()
            .map(|op| {
                let public_op = public_op_name(&manifest.plugin_id, &op.op_name);
                let service_instance_id = op
                    .service_id
                    .as_ref()
                    .and_then(|service_id| service_keys.get(service_id))
                    .map(PluginServiceKey::service_instance_id);
                (
                    public_op.clone(),
                    PluginOperationRoute {
                        plugin_id: manifest.plugin_id.clone(),
                        op_name: op.op_name.clone(),
                        public_op,
                        intent: op.intent,
                        auto_workspace_overlay: op.auto_workspace_overlay,
                        service_id: op.service_id.clone(),
                        service_instance_id,
                        timeout_ms: op.timeout_ms,
                    },
                )
            })
            .collect::<BTreeMap<_, _>>();
        let registered_ops = operation_routes.keys().cloned().collect::<Vec<_>>();
        let (services, service_processes) = if manifest.services.is_empty() {
            (Vec::new(), Vec::new())
        } else {
            let mut process_specs = Vec::new();
            let statuses = manifest
                .services
                .iter()
                .map(|service| {
                    let key = service_keys
                        .get(&service.service_id)
                        .ok_or_else(|| {
                            PluginError::Manifest(format!(
                                "service {} key was not prepared",
                                service.service_id
                            ))
                        })?
                        .clone();
                    let mut status = PluginServiceStatus::new(key.clone());
                    status.state = PluginServiceState::Stopped;
                    status.registered_ops = registered_ops.clone();
                    status.last_error = Some(
                        "process-backed PPC execution is not started in this slice".to_owned(),
                    );
                    if !service.command.is_empty() {
                        process_specs.push(process_spec(&key, service, &ppc_socket_root)?);
                    }
                    Ok(status)
                })
                .collect::<Result<Vec<_>, PluginError>>()?;
            (statuses, process_specs)
        };
        Ok(Self {
            plugin_id: manifest.plugin_id,
            plugin_digest: manifest.plugin_digest,
            registered_ops,
            operation_routes,
            services,
            service_processes,
            runtime_loaded: true,
        })
    }
}

fn process_spec(
    key: &PluginServiceKey,
    service: &PluginServiceManifest,
    ppc_socket_root: &str,
) -> Result<PluginProcessSpec, PluginError> {
    if ppc_socket_root == process::PLUGIN_PPC_ROOT {
        return PluginProcessSpec::new(
            key.clone(),
            service.command.clone(),
            service.ppc_protocol_version,
        );
    }
    PluginProcessSpec::new_with_socket_root(
        key.clone(),
        service.command.clone(),
        service.ppc_protocol_version,
        ppc_socket_root,
    )
}

fn service_keys_for_manifest(
    args: &Value,
    manifest: &PluginManifest,
) -> Result<BTreeMap<String, PluginServiceKey>, DaemonError> {
    if manifest.services.is_empty() {
        return Ok(BTreeMap::new());
    }
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
            Ok((service.service_id.clone(), key))
        })
        .collect::<Result<BTreeMap<_, _>, PluginError>>()
        .map_err(DaemonError::from)
}

fn ppc_socket_root(args: &Value) -> String {
    #[cfg(test)]
    {
        if let Some(root) = args.get("ppc_socket_root").and_then(Value::as_str) {
            return root.to_owned();
        }
    }
    let _ = args;
    process::PLUGIN_PPC_ROOT.to_owned()
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

fn process_values(processes: &[PluginProcessSpec]) -> Vec<Value> {
    processes.iter().map(PluginProcessSpec::to_json).collect()
}

fn connected_ppc_routes(state: &DaemonPluginState) -> Vec<String> {
    state
        .loaded
        .values()
        .flat_map(|loaded| loaded.operation_routes.values())
        .filter(|route| {
            route
                .service_instance_id
                .as_ref()
                .is_some_and(|service_instance_id| {
                    state.service_ppc_clients.contains_key(service_instance_id)
                })
        })
        .map(|route| route.public_op.clone())
        .collect()
}

fn connected_ppc_services(state: &DaemonPluginState) -> Vec<String> {
    state.service_ppc_clients.keys().cloned().collect()
}

fn start_service_processes(
    state: &mut DaemonPluginState,
    specs: &[PluginProcessSpec],
) -> Result<usize, DaemonError> {
    let mut started = 0;
    for spec in specs {
        let service_instance_id = spec.service_instance_id();
        if state.service_processes.contains_key(&service_instance_id) {
            continue;
        }
        let (process, client) = spec.spawn_connected(Duration::from_millis(
            ppc_router::DEFAULT_PLUGIN_PPC_TIMEOUT_MS,
        ))?;
        state
            .service_ppc_clients
            .insert(service_instance_id.clone(), client);
        state.service_processes.insert(service_instance_id, process);
        started += 1;
    }
    Ok(started)
}

fn stop_plugin_service_processes(state: &mut DaemonPluginState, plugin_id: &str) {
    let stale = state
        .loaded
        .get(plugin_id)
        .map(|loaded| {
            loaded
                .service_processes
                .iter()
                .map(PluginProcessSpec::service_instance_id)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    for service_instance_id in stale {
        state.service_processes.remove(&service_instance_id);
        state.service_ppc_clients.remove(&service_instance_id);
    }
}

fn running_process_values(state: &mut DaemonPluginState) -> Vec<Value> {
    let mut closed = Vec::new();
    let mut values = Vec::new();
    for (service_instance_id, process) in &mut state.service_processes {
        let status = process.status_json();
        if status["running"] != true {
            closed.push(service_instance_id.clone());
        }
        values.push(status);
    }
    for service_instance_id in closed {
        state.service_processes.remove(&service_instance_id);
    }
    values
}

fn route_for_op(op: &str) -> Result<Option<PluginOperationRoute>, DaemonError> {
    let state = lock_state()?;
    Ok(state
        .loaded
        .values()
        .find_map(|loaded| loaded.operation_routes.get(op).cloned()))
}

fn dispatch_registered_route(
    route: PluginOperationRoute,
    invocation_id: &str,
    args: &Value,
) -> Result<Value, DaemonError> {
    ensure_plugin_family_allowed(args)?;
    if route.intent == Intent::ReadOnly && route.service_id.is_some() {
        if let Some(response) = dispatch_connected_read_only_route(&route, invocation_id, args)? {
            return Ok(response);
        }
    }
    dispatch_deferred_route(route, args)
}

fn dispatch_connected_read_only_route(
    route: &PluginOperationRoute,
    invocation_id: &str,
    args: &Value,
) -> Result<Option<Value>, DaemonError> {
    let Some(service_instance_id) = route.service_instance_id.clone() else {
        return Ok(None);
    };
    let Some(mut client) = take_ppc_client(&service_instance_id)? else {
        return Ok(None);
    };
    let timeout = Duration::from_millis(
        route
            .timeout_ms
            .unwrap_or(ppc_router::DEFAULT_PLUGIN_PPC_TIMEOUT_MS),
    );
    let request = PpcEnvelope {
        message_id: invocation_id.to_owned(),
        direction: PpcDirection::Request,
        op: route.public_op.clone(),
        body: serde_json::to_string(args).map_err(|err| PluginError::Ppc(err.to_string()))?,
    };
    let reply = client.round_trip(&request, timeout);
    put_ppc_client(service_instance_id, client)?;
    let reply = reply?;
    let payload: Value =
        serde_json::from_str(&reply.body).map_err(|err| PluginError::Ppc(err.to_string()))?;
    if payload.is_object() {
        Ok(Some(payload))
    } else {
        Ok(Some(json!({
            "success": true,
            "result": payload,
        })))
    }
}

fn take_ppc_client(
    service_instance_id: &str,
) -> Result<Option<ppc_router::PpcClient>, DaemonError> {
    Ok(lock_state()?
        .service_ppc_clients
        .remove(service_instance_id))
}

fn put_ppc_client(
    service_instance_id: String,
    client: ppc_router::PpcClient,
) -> Result<(), DaemonError> {
    lock_state()?
        .service_ppc_clients
        .insert(service_instance_id, client);
    Ok(())
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
    use std::path::{Path, PathBuf};
    use std::sync::Mutex;
    use std::time::{Duration, Instant};

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

    fn lsp_manifest_with_command(digest: &str, op_name: &str, command: Vec<&str>) -> Value {
        let mut manifest = lsp_manifest(digest, op_name);
        manifest["services"][0]["command"] =
            Value::Array(command.into_iter().map(|item| json!(item)).collect());
        manifest
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
        assert_eq!(response["service_processes"][0]["service_id"], "pyright");
        assert!(response["service_processes"][0]["socket_path"]
            .as_str()
            .expect("socket path")
            .starts_with("/eos/plugin/ppc/"));

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

    #[test]
    fn connected_read_only_plugin_op_round_trips_over_ppc() {
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

        let (client_stream, mut server_stream) =
            std::os::unix::net::UnixStream::pair().expect("unix stream pair");
        register_ppc_client_for_tests("plugin.lsp.hover", client_stream);
        let server = std::thread::spawn(move || {
            let request = PpcEnvelope::decode(
                &ppc_router::read_frame(&mut server_stream).expect("read ppc request"),
            )
            .expect("decode ppc request");
            assert_eq!(request.message_id, "plugin-hover-test");
            assert_eq!(request.op, "plugin.lsp.hover");
            assert!(request.body.contains("agent-plugin"));
            let reply = PpcEnvelope {
                message_id: request.message_id,
                direction: PpcDirection::Reply,
                op: "reply".to_owned(),
                body: r#"{"success":true,"from_ppc":true}"#.to_owned(),
            };
            use std::io::Write;
            server_stream
                .write_all(&reply.encode().expect("encode reply"))
                .expect("write ppc reply");
        });

        let routed = table.dispatch(&Request {
            op: "plugin.lsp.hover".to_owned(),
            invocation_id: "plugin-hover-test".to_owned(),
            args: json!({"agent_id": "agent-plugin"}),
        });
        assert_eq!(routed["success"], true);
        assert_eq!(routed["from_ppc"], true);
        server.join().expect("server thread");
        reset_for_tests();
    }

    #[test]
    fn ensure_can_start_and_status_reports_service_process() {
        let _guard = TEST_LOCK.lock().expect("plugin test lock poisoned");
        reset_for_tests();
        let socket_root = test_socket_root("ensure-start");
        let connector = spawn_replying_connector(
            socket_root.clone(),
            r#"{"success":true,"from_started_service":true}"#,
        );
        let command = vec![
            "/bin/sh",
            "-c",
            "test \"$EOS_PLUGIN_SERVICE_ID\" = pyright && sleep 30",
        ];
        let response = op_ensure(
            &json!({
                "manifest": lsp_manifest_with_command("digest-a", "hover", command),
                "layer_stack_root": "/eos/plugin/layer-stack",
                "workspace_root": "/eos/plugin/workspace",
                "ppc_socket_root": socket_root,
                "start_services": true
            }),
            DispatchContext::empty(),
        )
        .expect("ensure response");

        assert_eq!(response["success"], true);
        assert_eq!(response["service_processes_started"], true);
        assert_eq!(
            response["running_service_processes"][0]["service_id"],
            "pyright"
        );
        assert_eq!(response["running_service_processes"][0]["running"], true);

        let status = op_status(&json!({}), DispatchContext::empty()).expect("status response");
        assert_eq!(
            status["running_service_processes"][0]["service_id"],
            "pyright"
        );
        assert_eq!(status["running_service_processes"][0]["running"], true);

        let table = OpTable::with_builtins();
        let routed = table.dispatch(&Request {
            op: "plugin.lsp.hover".to_owned(),
            invocation_id: "plugin-hover-started-service".to_owned(),
            args: json!({"agent_id": "agent-plugin"}),
        });
        assert_eq!(routed["success"], true, "routed response: {routed:?}");
        assert_eq!(routed["from_started_service"], true);

        connector.join().expect("connector thread");
        reset_for_tests();
    }

    fn test_socket_root(name: &str) -> PathBuf {
        let root = PathBuf::from("target").join(format!("ppc-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        root
    }

    fn spawn_replying_connector(
        socket_root: PathBuf,
        reply_body: &'static str,
    ) -> std::thread::JoinHandle<()> {
        std::thread::spawn(move || {
            let socket = wait_for_socket(&socket_root);
            let mut stream =
                std::os::unix::net::UnixStream::connect(socket).expect("connect ppc socket");
            let request = PpcEnvelope::decode(
                &ppc_router::read_frame(&mut stream).expect("read ppc request"),
            )
            .expect("decode ppc request");
            let reply = PpcEnvelope {
                message_id: request.message_id,
                direction: PpcDirection::Reply,
                op: "reply".to_owned(),
                body: reply_body.to_owned(),
            };
            use std::io::Write;
            stream
                .write_all(&reply.encode().expect("encode reply"))
                .expect("write ppc reply");
        })
    }

    fn wait_for_socket(root: &Path) -> PathBuf {
        let deadline = Instant::now() + Duration::from_secs(1);
        loop {
            if let Ok(entries) = std::fs::read_dir(root) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.extension().and_then(|ext| ext.to_str()) == Some("sock") {
                        return path;
                    }
                }
            }
            assert!(
                Instant::now() < deadline,
                "timed out waiting for socket under {}",
                root.display()
            );
            std::thread::sleep(Duration::from_millis(10));
        }
    }
}
