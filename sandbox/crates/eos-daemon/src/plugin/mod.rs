//! Daemon plugin API surface.
//!
//! This module owns the daemon-side `api.plugin.*` routes. It keeps the
//! contract-only `eos-plugin` crate free of sandbox publish edges while the
//! daemon owns service process lifetime, PPC dispatch, manifest refresh,
//! plugin-originated OCC callbacks, and oneshot overlay execution.

mod ensure_args;
mod occ_callbacks;
mod overlay;
mod ppc_router;
mod process;
mod service;

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
use std::time::{Duration, Instant};

use eos_plugin::{
    PluginError, PluginServiceKey, PluginServiceState, PluginServiceStatus, PpcDirection,
    PpcEnvelope, RefreshAck, RefreshRequest, RefreshStrategy, ServiceMode,
};
use eos_protocol::Intent;
use serde_json::{json, Value};

use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;
use crate::response_timings::u64_to_f64_saturating;
use ensure_args::{loaded_matches_parsed, validate_plugin_caller_fields, ParsedEnsure};
use overlay::PluginOverlayCommand;
use process::PluginProcessSpec;
use service::{
    acquire_service_snapshot, active_manifest_key, insert_started_service_processes,
    mark_service_ready, mark_service_restarted, mark_service_stale, mark_service_stopped,
    release_service_snapshot, running_process_values, service_specs_to_start, service_status_mut,
    spawn_service_processes, stop_plugin_service_processes,
    stop_services_for_layer_stack_root as stop_services_for_layer_stack_root_in_state,
    PluginServiceSnapshot,
};

type SharedPpcClient = Arc<ppc_router::PpcClient>;
const MAX_PLUGIN_RESPONSE_BYTES: usize = 8 * 1024 * 1024;
const MAX_PLUGIN_CALLER_FIELD_CHARS: usize = 256;

const WORKSPACE_SNAPSHOT_REFRESH_OP: &str = "daemon.workspace_snapshot_refresh";

#[derive(Debug, Clone)]
struct LoadedPluginRuntime {
    digest: String,
    registered_ops: Vec<String>,
    operation_routes: BTreeMap<String, PluginOperationRoute>,
    services: Vec<PluginServiceStatus>,
    service_processes: Vec<PluginProcessSpec>,
    runtime_loaded: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct PluginOperationRoute {
    plugin_id: String,
    op_name: String,
    public_op: String,
    layer_stack_root: Option<String>,
    intent: Intent,
    auto_workspace_overlay: bool,
    service_id: Option<String>,
    service_instance_id: Option<String>,
    service_key: Option<PluginServiceKey>,
    service_mode: Option<ServiceMode>,
    service_command: Vec<String>,
    service_ppc_protocol_version: Option<u32>,
    timeout_ms: Option<u64>,
}

impl PluginOperationRoute {
    const fn dispatch_mode(&self) -> &'static str {
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
            "layer_stack_root": self.layer_stack_root,
            "intent": self.intent,
            "auto_workspace_overlay": self.auto_workspace_overlay,
            "service_id": self.service_id,
            "service_instance_id": self.service_instance_id,
            "service_mode": self.service_mode,
            "service_command": self.service_command,
            "timeout_ms": self.timeout_ms,
            "dispatch_mode": self.dispatch_mode(),
        })
    }
}

#[derive(Debug, Default)]
struct DaemonPluginState {
    loaded: BTreeMap<String, LoadedPluginRuntime>,
    service_ppc_clients: BTreeMap<String, SharedPpcClient>,
    service_processes: BTreeMap<String, process::PluginServiceProcess>,
    service_snapshots: BTreeMap<String, PluginServiceSnapshot>,
    service_refresh_locks: BTreeMap<String, Arc<Mutex<()>>>,
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

pub fn op_ensure(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    ensure_plugin_family_allowed(args)?;

    let parsed = ParsedEnsure::from_args(args)?;
    let start_services = args
        .get("start_services")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let (already_loaded, specs_to_start) = {
        let mut state = lock_state()?;
        let already_loaded = state
            .loaded
            .get(&parsed.plugin_id)
            .is_some_and(|loaded| loaded_matches_parsed(loaded, &parsed));
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
        let specs_to_start = if start_services {
            service_specs_to_start(&state, &process_specs)
        } else {
            Vec::new()
        };
        drop(state);
        (already_loaded, specs_to_start)
    };
    let started_services = spawn_service_processes(&specs_to_start)?;
    let mut state = lock_state()?;
    let started_count = insert_started_service_processes(&mut state, started_services)?;
    let loaded = state.loaded.get(&parsed.plugin_id).ok_or_else(|| {
        DaemonError::Plugin(PluginError::Ensure(format!(
            "plugin {} was not recorded after ensure",
            parsed.plugin_id
        )))
    })?;
    let digest = loaded.digest.clone();
    let registered_ops = loaded.registered_ops.clone();
    let runtime_loaded = loaded.runtime_loaded;
    let operation_routes = route_values(&loaded.operation_routes);
    let services = loaded.services.clone();
    let service_processes = process_values(&loaded.service_processes);
    let running_service_processes = running_process_values(&mut state);
    let connected_ppc_routes = connected_ppc_routes(&state);
    let connected_ppc_services = connected_ppc_services(&state);
    drop(state);

    Ok(json!({
        "success": true,
        "plugin": parsed.plugin_id,
        "digest": digest,
        "registered_ops": registered_ops,
        "runtime_loaded": runtime_loaded,
        "runtime_warmed": false,
        "service_processes_started": started_count > 0,
        "started_service_process_count": started_count,
        "already_loaded": already_loaded,
        "operation_routes": operation_routes,
        "services": services,
        "service_processes": service_processes,
        "running_service_processes": running_service_processes,
        "connected_ppc_routes": connected_ppc_routes,
        "connected_ppc_services": connected_ppc_services,
    }))
}

pub fn op_status(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    ensure_plugin_family_allowed(args)?;
    let probe_services = args
        .get("probe_services")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let probe_timeout = Duration::from_millis(
        args.get("probe_timeout_ms")
            .and_then(Value::as_u64)
            .unwrap_or(ppc_router::DEFAULT_PLUGIN_PPC_TIMEOUT_MS),
    );
    let probe_targets = {
        let mut state = lock_state()?;
        let _ = running_process_values(&mut state);
        if probe_services {
            service_health_probe_targets(&state)
        } else {
            Vec::new()
        }
    };
    let service_health = probe_service_health(probe_targets, probe_timeout);
    let mut state = lock_state()?;
    let running_service_processes = running_process_values(&mut state);
    let loaded_plugins = loaded_plugin_values(&state);
    let connected_ppc_routes = connected_ppc_routes(&state);
    let connected_ppc_services = connected_ppc_services(&state);
    drop(state);
    Ok(json!({
        "success": true,
        "loaded_plugins": loaded_plugins,
        "running_service_processes": running_service_processes,
        "connected_ppc_routes": connected_ppc_routes,
        "connected_ppc_services": connected_ppc_services,
        "service_health": service_health,
        "pending": [],
    }))
}

pub fn dispatch_registered_op(
    op: &str,
    invocation_id: &str,
    args: &Value,
    _context: DispatchContext<'_>,
) -> Option<Result<Value, DaemonError>> {
    if !op.starts_with("plugin.") {
        return None;
    }
    if let Err(err) = ensure_plugin_family_allowed(args) {
        return Some(Err(err));
    }
    let route = match route_for_op(op) {
        Ok(Some(route)) => route,
        Ok(None) => return None,
        Err(err) => return Some(Err(err)),
    };
    Some(dispatch_registered_route(&route, invocation_id, args))
}

#[cfg(test)]
fn reset_for_tests() {
    if let Ok(mut state) = state_cell().lock() {
        let snapshots = state
            .service_snapshots
            .values()
            .cloned()
            .collect::<Vec<_>>();
        state.loaded.clear();
        state.service_ppc_clients.clear();
        state.service_processes.clear();
        state.service_snapshots.clear();
        state.service_refresh_locks.clear();
        drop(state);
        for snapshot in snapshots {
            release_service_snapshot(&snapshot);
        }
    }
}

#[cfg(test)]
fn register_ppc_client_for_tests(
    op: &str,
    stream: std::os::unix::net::UnixStream,
) -> Result<(), DaemonError> {
    let mut state = lock_state()?;
    let (service_instance_id, manifest_key) = state
        .loaded
        .values()
        .find_map(|loaded| loaded.operation_routes.get(op))
        .map_or_else(
            || (op.to_owned(), None),
            |route| {
                let manifest_key = route
                    .service_key
                    .as_ref()
                    .and_then(|key| active_manifest_key(&key.layer_stack_root).ok());
                (
                    route
                        .service_instance_id
                        .clone()
                        .unwrap_or_else(|| op.to_owned()),
                    manifest_key,
                )
            },
        );
    if let Some(manifest_key) = manifest_key {
        if let Ok(status) = service_status_mut(&mut state, &service_instance_id) {
            status.state = PluginServiceState::Ready;
            status.manifest_key = Some(manifest_key);
            status.last_error = None;
        }
    }
    state.service_ppc_clients.insert(
        service_instance_id,
        Arc::new(ppc_router::PpcClient::new(stream)?),
    );
    drop(state);
    Ok(())
}

fn ensure_plugin_family_allowed(args: &Value) -> Result<(), DaemonError> {
    validate_plugin_caller_fields(args)?;
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

pub(crate) fn stop_services_for_layer_stack_root(
    layer_stack_root: &str,
) -> Result<usize, DaemonError> {
    let mut state = lock_state()?;
    Ok(stop_services_for_layer_stack_root_in_state(
        &mut state,
        layer_stack_root,
    ))
}

fn loaded_plugin_values(state: &DaemonPluginState) -> Vec<Value> {
    state
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
        .collect()
}

#[derive(Debug, Clone)]
struct ServiceHealthProbeTarget {
    plugin_id: String,
    service_id: String,
    service_instance_id: String,
    manifest_key: String,
    client: SharedPpcClient,
}

fn service_health_probe_targets(state: &DaemonPluginState) -> Vec<ServiceHealthProbeTarget> {
    state
        .loaded
        .values()
        .flat_map(|loaded| loaded.services.iter())
        .filter_map(|status| {
            let service_instance_id = status.key.service_instance_id();
            let client = state.service_ppc_clients.get(&service_instance_id)?;
            let snapshot = state.service_snapshots.get(&service_instance_id)?;
            Some(ServiceHealthProbeTarget {
                plugin_id: status.key.plugin_id.clone(),
                service_id: status.key.service_id.clone(),
                service_instance_id,
                manifest_key: snapshot.manifest_key.clone(),
                client: Arc::clone(client),
            })
        })
        .collect()
}

fn probe_service_health(targets: Vec<ServiceHealthProbeTarget>, timeout: Duration) -> Vec<Value> {
    targets
        .into_iter()
        .enumerate()
        .map(
            |(index, target)| match probe_connected_service_health(&target, index, timeout) {
                Ok(health) => health,
                Err(err) => {
                    let error = err.to_string();
                    let teardown_error =
                        teardown_failed_connected_service(&target.service_instance_id, &error)
                            .err()
                            .map(|err| err.to_string());
                    json!({
                        "success": false,
                        "plugin": target.plugin_id,
                        "service_id": target.service_id,
                        "service_instance_id": target.service_instance_id,
                        "manifest_key": target.manifest_key,
                        "error": error,
                        "teardown_error": teardown_error,
                    })
                }
            },
        )
        .collect()
}

fn probe_connected_service_health(
    target: &ServiceHealthProbeTarget,
    index: usize,
    timeout: Duration,
) -> Result<Value, DaemonError> {
    let request = RefreshRequest::Health {
        manifest_key: target.manifest_key.clone(),
    };
    let envelope = PpcEnvelope {
        message_id: format!("api.plugin.status:health:{index}"),
        direction: PpcDirection::Request,
        op: WORKSPACE_SNAPSHOT_REFRESH_OP.to_owned(),
        body: serde_json::to_string(&request).map_err(|err| PluginError::Ppc(err.to_string()))?,
    };
    let reply = target.client.round_trip(&envelope, timeout)?;
    let ack: RefreshAck =
        serde_json::from_str(&reply.body).map_err(|err| PluginError::Ppc(err.to_string()))?;
    ack.require_manifest(&target.manifest_key)?;
    Ok(json!({
        "success": true,
        "plugin": target.plugin_id,
        "service_id": target.service_id,
        "service_instance_id": target.service_instance_id,
        "manifest_key": target.manifest_key,
        "accepted": ack.accepted,
    }))
}

fn route_for_op(op: &str) -> Result<Option<PluginOperationRoute>, DaemonError> {
    let state = lock_state()?;
    Ok(state
        .loaded
        .values()
        .find_map(|loaded| loaded.operation_routes.get(op).cloned()))
}

fn dispatch_registered_route(
    route: &PluginOperationRoute,
    invocation_id: &str,
    args: &Value,
) -> Result<Value, DaemonError> {
    ensure_plugin_family_allowed(args)?;
    if route.intent == Intent::ReadOnly && route.service_id.is_some() {
        if let Some(response) = dispatch_connected_read_only_route(route, invocation_id, args)? {
            return Ok(response);
        }
    }
    if route.intent == Intent::WriteAllowed && route.auto_workspace_overlay {
        if let Some(response) = dispatch_oneshot_overlay_route(route, invocation_id, args)? {
            return Ok(response);
        }
    }
    if route.intent == Intent::WriteAllowed
        && !route.auto_workspace_overlay
        && route.service_id.is_some()
    {
        if let Some(response) = dispatch_connected_self_managed_route(route, invocation_id, args)? {
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
    let Some(client) = ensure_connected_service_current(route, invocation_id)? else {
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
    let reply = match reply {
        Ok(reply) => reply,
        Err(err) => {
            teardown_failed_connected_service(&service_instance_id, &err.to_string())?;
            return Err(err);
        }
    };
    response_payload_from_reply(&reply)
}

fn ensure_connected_service_current(
    route: &PluginOperationRoute,
    invocation_id: &str,
) -> Result<Option<SharedPpcClient>, DaemonError> {
    let Some(service_instance_id) = route.service_instance_id.as_deref() else {
        return Ok(None);
    };
    ensure_tracked_service_process_running(service_instance_id)?;
    let Some(service_key) = route.service_key.as_ref() else {
        let Some(client) = ppc_client_for_service(service_instance_id)? else {
            return Ok(None);
        };
        return Ok(Some(client));
    };
    if route.service_mode != Some(ServiceMode::WorkspaceSnapshotRefresh) {
        let Some(client) = ppc_client_for_service(service_instance_id)? else {
            return Ok(None);
        };
        return Ok(Some(client));
    }

    if let Some(client) = ppc_client_for_service(service_instance_id)? {
        let target_manifest_key = active_manifest_key(&service_key.layer_stack_root)?;
        if service_is_ready_on_manifest(service_instance_id, &target_manifest_key)? {
            return Ok(Some(client));
        }
    } else if !service_was_started_before(service_instance_id)? {
        return Ok(None);
    }

    // Refresh mutates the service namespace and snapshot lease, so it is
    // singleflight per service. Operation dispatch remains multiplexed after
    // this freshness gate returns.
    let refresh_lock = refresh_lock_for_service(service_instance_id)?;
    let _refresh_guard = refresh_lock
        .lock()
        .map_err(|_| DaemonError::StateLockPoisoned("plugin service refresh"))?;
    ensure_tracked_service_process_running(service_instance_id)?;
    let Some(client) = ppc_client_for_service(service_instance_id)? else {
        if service_was_started_before(service_instance_id)? {
            return restart_read_only_service(service_instance_id);
        }
        return Ok(None);
    };
    let target_manifest_key = active_manifest_key(&service_key.layer_stack_root)?;
    if service_is_ready_on_manifest(service_instance_id, &target_manifest_key)? {
        return Ok(Some(client));
    }
    if service_key.refresh_strategy == RefreshStrategy::RestartService {
        return restart_read_only_service(service_instance_id);
    }

    refresh_connected_service(
        route,
        service_key,
        service_instance_id,
        &client,
        invocation_id,
    )?;
    Ok(Some(client))
}

fn refresh_lock_for_service(service_instance_id: &str) -> Result<Arc<Mutex<()>>, DaemonError> {
    let mut state = lock_state()?;
    Ok(state
        .service_refresh_locks
        .entry(service_instance_id.to_owned())
        .or_insert_with(|| Arc::new(Mutex::new(())))
        .clone())
}

fn service_was_started_before(service_instance_id: &str) -> Result<bool, DaemonError> {
    let state = lock_state()?;
    Ok(state
        .loaded
        .values()
        .flat_map(|loaded| loaded.services.iter())
        .find(|status| status.key.service_instance_id() == service_instance_id)
        .is_some_and(|status| status.manifest_key.is_some()))
}

fn service_is_ready_on_manifest(
    service_instance_id: &str,
    target_manifest_key: &str,
) -> Result<bool, DaemonError> {
    let state = lock_state()?;
    Ok(state
        .loaded
        .values()
        .flat_map(|loaded| loaded.services.iter())
        .find(|status| status.key.service_instance_id() == service_instance_id)
        .is_some_and(|status| {
            status
                .require_ready_on_manifest(target_manifest_key)
                .is_ok()
        }))
}

fn refresh_connected_service(
    route: &PluginOperationRoute,
    service_key: &PluginServiceKey,
    service_instance_id: &str,
    client: &SharedPpcClient,
    invocation_id: &str,
) -> Result<(), DaemonError> {
    let snapshot = acquire_service_snapshot(service_key, "refresh")?;
    let timeout = Duration::from_millis(
        route
            .timeout_ms
            .unwrap_or(ppc_router::DEFAULT_PLUGIN_PPC_TIMEOUT_MS),
    );
    let refresh_result = {
        send_refresh_sequence(
            client,
            service_key,
            service_instance_id,
            invocation_id,
            &snapshot,
            timeout,
        )
    };
    if let Err(err) = refresh_result {
        release_service_snapshot(&snapshot);
        let mut state = lock_state()?;
        let _ = mark_service_stale(&mut state, service_instance_id, err.to_string());
        return Err(err);
    }

    let old_snapshot = {
        let mut state = lock_state()?;
        mark_service_ready(&mut state, service_instance_id, &snapshot, true)?;
        state
            .service_snapshots
            .insert(service_instance_id.to_owned(), snapshot)
    };
    if let Some(old_snapshot) = old_snapshot {
        release_service_snapshot(&old_snapshot);
    }
    Ok(())
}

fn send_refresh_sequence(
    client: &ppc_router::PpcClient,
    service_key: &PluginServiceKey,
    service_instance_id: &str,
    invocation_id: &str,
    snapshot: &PluginServiceSnapshot,
    timeout: Duration,
) -> Result<(), DaemonError> {
    let request_id = format!("{invocation_id}:refresh");
    send_refresh_request(
        client,
        invocation_id,
        0,
        &RefreshRequest::PrepareRefresh {
            target_manifest_key: snapshot.manifest_key.clone(),
        },
        snapshot,
        timeout,
    )?;
    send_refresh_request(
        client,
        invocation_id,
        1,
        &RefreshRequest::Quiesce {
            request_id: request_id.clone(),
        },
        snapshot,
        timeout,
    )?;
    remount_connected_service_workspace(service_instance_id, service_key, snapshot, timeout)?;

    let mut requests = vec![RefreshRequest::SwapWorkspace {
        layer_paths: snapshot.layer_paths.clone(),
        workspace_root: service_key.workspace_root.clone(),
        manifest_key: snapshot.manifest_key.clone(),
    }];
    if service_key.refresh_strategy == RefreshStrategy::RemountWorkspaceAndNotify {
        requests.push(RefreshRequest::NotifyRefresh {
            changed_paths: Vec::new(),
            full_resync: true,
        });
    }
    requests.push(RefreshRequest::Resume { request_id });
    requests.push(RefreshRequest::Health {
        manifest_key: snapshot.manifest_key.clone(),
    });

    for (index, request) in requests.iter().enumerate() {
        send_refresh_request(client, invocation_id, index + 2, request, snapshot, timeout)?;
    }
    Ok(())
}

fn remount_connected_service_workspace(
    service_instance_id: &str,
    service_key: &PluginServiceKey,
    snapshot: &PluginServiceSnapshot,
    timeout: Duration,
) -> Result<(), DaemonError> {
    let Some(overlay) = snapshot.overlay.as_ref() else {
        return Ok(());
    };
    let target_pid = service_process_pid(service_instance_id)?;
    process::remount_workspace_overlay(target_pid, &service_key.workspace_root, overlay, timeout)
}

fn service_process_pid(service_instance_id: &str) -> Result<u32, DaemonError> {
    let pid = {
        let mut state = lock_state()?;
        let process = state
            .service_processes
            .get_mut(service_instance_id)
            .ok_or_else(|| {
                DaemonError::Plugin(PluginError::Ensure(format!(
                    "service {service_instance_id} process is not running for workspace remount"
                )))
            })?;
        if process.status_json()["running"] != true {
            return Err(DaemonError::Plugin(PluginError::Ensure(format!(
                "service {service_instance_id} process exited before workspace remount"
            ))));
        }
        let pid = process.pid();
        drop(state);
        pid
    };
    Ok(pid)
}

fn send_refresh_request(
    client: &ppc_router::PpcClient,
    invocation_id: &str,
    index: usize,
    request: &RefreshRequest,
    snapshot: &PluginServiceSnapshot,
    timeout: Duration,
) -> Result<(), DaemonError> {
    let envelope = PpcEnvelope {
        message_id: format!("{invocation_id}:refresh:{index}"),
        direction: PpcDirection::Request,
        op: WORKSPACE_SNAPSHOT_REFRESH_OP.to_owned(),
        body: serde_json::to_string(&request).map_err(|err| PluginError::Ppc(err.to_string()))?,
    };
    let reply = client.round_trip(&envelope, timeout)?;
    let ack: RefreshAck =
        serde_json::from_str(&reply.body).map_err(|err| PluginError::Ppc(err.to_string()))?;
    ack.require_manifest(&snapshot.manifest_key)?;
    Ok(())
}

fn restart_read_only_service(
    service_instance_id: &str,
) -> Result<Option<SharedPpcClient>, DaemonError> {
    let (spec, old_snapshot) = {
        let mut state = lock_state()?;
        let spec = state
            .loaded
            .values()
            .flat_map(|loaded| loaded.service_processes.iter())
            .find(|spec| spec.service_instance_id() == service_instance_id)
            .cloned();
        state.service_processes.remove(service_instance_id);
        state.service_ppc_clients.remove(service_instance_id);
        (spec, state.service_snapshots.remove(service_instance_id))
    };
    let Some(spec) = spec else {
        return Ok(None);
    };
    if let Some(old_snapshot) = old_snapshot {
        release_service_snapshot(&old_snapshot);
    }
    let started = spawn_service_processes(&[spec])?;
    let mut state = lock_state()?;
    insert_started_service_processes(&mut state, started)?;
    mark_service_restarted(&mut state, service_instance_id)?;
    Ok(state.service_ppc_clients.get(service_instance_id).cloned())
}

fn dispatch_oneshot_overlay_route(
    route: &PluginOperationRoute,
    invocation_id: &str,
    args: &Value,
) -> Result<Option<Value>, DaemonError> {
    if route.service_mode != Some(ServiceMode::OneshotOverlay) {
        return Ok(None);
    }
    let Some(layer_stack_root) = route.layer_stack_root.clone() else {
        return Ok(None);
    };
    let Some(service_key) = route.service_key.clone() else {
        return Ok(None);
    };
    if route.service_command.is_empty() {
        return Ok(None);
    }
    let agent_id = args
        .get("agent_id")
        .and_then(Value::as_str)
        .unwrap_or("default")
        .to_owned();
    let mut env = BTreeMap::from([
        (
            "EOS_PLUGIN_LAYER_STACK_ROOT".to_owned(),
            service_key.layer_stack_root,
        ),
        (
            "EOS_PLUGIN_WORKSPACE_ROOT".to_owned(),
            service_key.workspace_root,
        ),
        ("EOS_PLUGIN_ID".to_owned(), service_key.plugin_id),
        ("EOS_PLUGIN_DIGEST".to_owned(), service_key.plugin_digest),
        ("EOS_PLUGIN_SERVICE_ID".to_owned(), service_key.service_id),
        (
            "EOS_PLUGIN_SERVICE_PROFILE_DIGEST".to_owned(),
            service_key.service_profile_digest,
        ),
        (
            "EOS_PLUGIN_PPC_PROTOCOL_VERSION".to_owned(),
            route.service_ppc_protocol_version.unwrap_or(1).to_string(),
        ),
        (
            "EOS_PLUGIN_SERVICE_MODE".to_owned(),
            "oneshot_overlay".to_owned(),
        ),
    ]);
    env.insert("EOS_PLUGIN_PUBLIC_OP".to_owned(), route.public_op.clone());
    let timeout_seconds = route
        .timeout_ms
        .map(|timeout| u64_to_f64_saturating(timeout) / 1000.0);
    let overlay_command = PluginOverlayCommand {
        layer_stack_root: PathBuf::from(layer_stack_root),
        invocation_id: invocation_id.to_owned(),
        agent_id,
        public_op: route.public_op.clone(),
        plugin_id: route.plugin_id.clone(),
        op_name: route.op_name.clone(),
        command: route.service_command.clone(),
        env,
        timeout_seconds,
    };
    Ok(Some(overlay::run_plugin_overlay_command(
        &overlay_command,
        args,
        Instant::now(),
    )?))
}

fn dispatch_connected_self_managed_route(
    route: &PluginOperationRoute,
    invocation_id: &str,
    args: &Value,
) -> Result<Option<Value>, DaemonError> {
    let Some(service_instance_id) = route.service_instance_id.clone() else {
        return Ok(None);
    };
    let Some(layer_stack_root) = route.layer_stack_root.clone() else {
        return Ok(None);
    };
    let Some(client) = ensure_connected_service_current(route, invocation_id)? else {
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
    let expected_root = PathBuf::from(layer_stack_root);
    let reply = client.round_trip_with_callbacks(&request, timeout, move |callback| {
        occ_callbacks::handle_callback_for_root(&expected_root, callback)
    });
    let reply = match reply {
        Ok(reply) => reply,
        Err(err) => {
            teardown_failed_connected_service(&service_instance_id, &err.to_string())?;
            return Err(err);
        }
    };
    response_payload_from_reply(&reply)
}

fn response_payload_from_reply(reply: &PpcEnvelope) -> Result<Option<Value>, DaemonError> {
    if reply.body.len() > MAX_PLUGIN_RESPONSE_BYTES {
        return Err(DaemonError::Plugin(PluginError::Ppc(format!(
            "plugin response exceeds {MAX_PLUGIN_RESPONSE_BYTES} byte limit"
        ))));
    }
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

fn ppc_client_for_service(
    service_instance_id: &str,
) -> Result<Option<SharedPpcClient>, DaemonError> {
    Ok(lock_state()?
        .service_ppc_clients
        .get(service_instance_id)
        .cloned())
}

fn ensure_tracked_service_process_running(service_instance_id: &str) -> Result<(), DaemonError> {
    let snapshot_to_release = {
        let mut state = lock_state()?;
        let Some(process) = state.service_processes.get_mut(service_instance_id) else {
            return Ok(());
        };
        if process.status_json()["running"] == true {
            return Ok(());
        }
        state.service_processes.remove(service_instance_id);
        state.service_ppc_clients.remove(service_instance_id);
        let snapshot = state.service_snapshots.remove(service_instance_id);
        mark_service_stopped(&mut state, service_instance_id);
        drop(state);
        snapshot
    };
    if let Some(snapshot) = snapshot_to_release {
        release_service_snapshot(&snapshot);
    }
    Err(DaemonError::Plugin(PluginError::Ensure(format!(
        "service {service_instance_id} process exited before plugin dispatch"
    ))))
}

fn teardown_failed_connected_service(
    service_instance_id: &str,
    reason: &str,
) -> Result<(), DaemonError> {
    let (process, snapshot) = {
        let mut state = lock_state()?;
        state.service_ppc_clients.remove(service_instance_id);
        let process = state.service_processes.remove(service_instance_id);
        let snapshot = state.service_snapshots.remove(service_instance_id);
        if let Ok(status) = service_status_mut(&mut state, service_instance_id) {
            status.state = PluginServiceState::Stopped;
            status.last_error = Some(reason.to_owned());
        }
        drop(state);
        (process, snapshot)
    };
    if let Some(mut process) = process {
        process.teardown();
    }
    if let Some(snapshot) = snapshot {
        release_service_snapshot(&snapshot);
    }
    Ok(())
}

fn dispatch_deferred_route(
    route: &PluginOperationRoute,
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
            "message": "plugin service is not connected for this route",
            "details": {
                "op": route.public_op,
                "dispatch_mode": route.dispatch_mode(),
            },
        },
    }))
}

#[cfg(test)]
#[path = "../../tests/plugin/mod.rs"]
mod tests;
