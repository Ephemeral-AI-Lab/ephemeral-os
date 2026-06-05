//! Op routing: the `OP_TABLE`, envelope validation, and the per-op handlers.
//!
//! The daemon decodes one [`eos_protocol::Request`] and routes `op` through the
//! [`OpTable`]. Handlers return a JSON `Value` response; a failure becomes the
//! structured error envelope ([`error_envelope`]) keyed by an
//! [`eos_protocol::ErrorKind`]. There is NO `ping` op — liveness is
//! `api.v1.heartbeat`, readiness is `api.runtime.ready`.
//!
//! Only the daemon-owned ops this phase wires are declared here:
//! `api.runtime.ready` (probes `control_plane` / `data_plane` / `mutation_gate`),
//! `api.v1.heartbeat`, `api.layer_metrics`, `api.audit.{pull,snapshot,reset_floor}`
//! (floor-reset gated by [`AUDIT_ALLOW_FLOOR_RESET_ENV`]). The full op table
//! (workspace-tool, isolated-workspace, plugin, layer-stack control) folds in at
//! port time through the same routing.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use eos_layerstack::{
    build_workspace_base, ensure_workspace_base, read_workspace_binding, require_workspace_binding,
    LayerStack,
};
use eos_protocol::{ErrorKind, Request};
#[cfg(test)]
use eos_protocol::{LayerChange, LayerPath};

use crate::audit_events::emit_dispatch_audit;
#[cfg(test)]
use crate::audit_events::{background_event_kind, emit_auto_squash_audit, uses_overlay_or_lease};
#[cfg(test)]
use crate::audit_ops::{op_audit_pull, op_audit_snapshot};
use crate::error::DaemonError;
use crate::invocation_registry::InFlightRegistry;
use crate::occ_writer::occ_service_cache_snapshot;
#[cfg(test)]
use crate::occ_writer::{
    base_hashes_for_snapshot, hash_bytes, normalize_root_key, occ_route_metrics,
    LayerStackCommitTransaction, LayerStackRouteProvider, OccServiceCache, OCC_SERVICE_CACHE_MAX,
};
use crate::request_args::{binding_to_value, require_string, timings_to_value_map};
#[cfg(test)]
use crate::response_timings::{
    i64_to_f64_saturating, insert_tree_resource_timings, resource_timings, TreeResourceStats,
};
#[cfg(test)]
use eos_occ::{
    CommitQueue, CommitTransactionPort, OccRouteProvider, OccService, OccStatus, PreparedChangeset,
    Route,
};

/// Env gate for `api.audit.reset_floor` (must be `"true"`).
pub const AUDIT_ALLOW_FLOOR_RESET_ENV: &str = "EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET";

/// A synchronous op handler: decoded args -> response value.
///
/// The Python handlers are a mix of sync + async; the Rust dispatcher resolves
/// that at the call site. The daemon keeps the routing surface explicit here
/// and lets command/file/isolated handlers own their runtime details.
type Handler = for<'ctx> fn(&Value, DispatchContext<'ctx>) -> Result<Value, DaemonError>;

/// Per-dispatch daemon services used by handlers that need runtime state.
#[derive(Clone, Copy, Default)]
pub struct DispatchContext<'ctx> {
    invocation_registry: Option<&'ctx InFlightRegistry>,
    read_request_s: Option<f64>,
}

impl<'ctx> DispatchContext<'ctx> {
    /// Empty context for direct unit dispatch.
    #[must_use]
    pub const fn empty() -> Self {
        Self {
            invocation_registry: None,
            read_request_s: None,
        }
    }

    /// Context carrying the server's invocation registry.
    #[must_use]
    pub const fn with_invocation_registry(invocation_registry: &'ctx InFlightRegistry) -> Self {
        Self {
            invocation_registry: Some(invocation_registry),
            read_request_s: None,
        }
    }

    /// Context carrying the server's invocation registry and measured request
    /// read duration.
    #[must_use]
    pub const fn with_invocation_registry_and_read_timing(
        invocation_registry: &'ctx InFlightRegistry,
        read_request_s: f64,
    ) -> Self {
        Self {
            invocation_registry: Some(invocation_registry),
            read_request_s: Some(read_request_s),
        }
    }
}

/// The op routing table.
///
/// Re-registering the same handler under an op is a no-op; a different handler
/// under a claimed op is rejected so peer collisions surface.
#[derive(Clone, Default)]
pub struct OpTable {
    handlers: HashMap<String, Handler>,
}

impl OpTable {
    /// Build the table pre-populated with the daemon-owned builtin ops this
    /// phase wires (NO `ping`).
    pub fn with_builtins() -> Self {
        let mut table = Self::default();
        // The real registration also folds in plugin ops and the full
        // isolated-workspace implementation; this table pins public daemon op
        // names as they are ported so callers never see unknown_op drift.
        table.register_builtin("api.runtime.ready", op_runtime_ready);
        table.register_builtin("api.v1.cancel", op_cancel);
        table.register_builtin("api.v1.heartbeat", op_heartbeat);
        table.register_builtin("api.v1.inflight_count", op_inflight_count);
        table.register_builtin("api.layer_metrics", op_layer_metrics);
        table.register_builtin("api.ensure_workspace_base", op_ensure_workspace_base);
        table.register_builtin("api.build_workspace_base", op_build_workspace_base);
        table.register_builtin("api.commit_to_workspace", op_commit_to_workspace);
        table.register_builtin("api.workspace_binding", op_workspace_binding);
        table.register_builtin("api.audit.pull", crate::audit_ops::op_audit_pull);
        table.register_builtin("api.audit.snapshot", crate::audit_ops::op_audit_snapshot);
        table.register_builtin(
            "api.audit.reset_floor",
            crate::audit_ops::op_audit_reset_floor,
        );
        table.register_builtin("api.v1.read_file", crate::workspace_ops::op_read_file);
        table.register_builtin("api.v1.write_file", crate::workspace_ops::op_write_file);
        table.register_builtin("api.v1.edit_file", crate::workspace_ops::op_edit_file);
        table.register_builtin("api.plugin.ensure", crate::plugin::op_ensure);
        table.register_builtin("api.plugin.status", crate::plugin::op_status);
        table.register_builtin("api.isolated_workspace.enter", crate::isolated::op_enter);
        table.register_builtin("api.isolated_workspace.exit", crate::isolated::op_exit);
        table.register_builtin("api.isolated_workspace.status", crate::isolated::op_status);
        table.register_builtin(
            "api.isolated_workspace.list_open",
            crate::isolated::op_list_open,
        );
        table.register_builtin(
            "api.isolated_workspace.test_reset",
            crate::isolated::op_test_reset,
        );
        table.register_builtin("api.v1.exec_command", crate::command::op_exec_command);
        table.register_builtin("api.v1.write_stdin", crate::command::op_command_write_stdin);
        table.register_builtin("api.v1.command.cancel", crate::command::op_command_cancel);
        table.register_builtin(
            "api.v1.command.collect_completed",
            crate::command::op_command_collect_completed,
        );
        table.register_builtin(
            "api.v1.command_session_count",
            crate::command::op_command_session_count,
        );
        table
    }

    /// Register `handler` under `op`.
    ///
    /// Returns `true` when the handler was inserted or already registered.
    /// Returns `false` when `op` is already claimed by a different handler,
    /// leaving the original route intact.
    #[must_use = "registration collisions are rejected; callers must check the result"]
    fn register(&mut self, op: &str, handler: Handler) -> bool {
        if let Some(existing) = self.handlers.get(op) {
            return std::ptr::fn_addr_eq(*existing, handler);
        }
        self.handlers.insert(op.to_owned(), handler);
        true
    }

    fn register_builtin(&mut self, op: &str, handler: Handler) {
        assert!(
            self.register(op, handler),
            "builtin op registered with a different handler: {op}"
        );
    }

    /// Route `request` to its handler, returning the response value or an error
    /// envelope value. Validates the envelope, runs the handler, and on an
    /// unknown op returns the `unknown_op` envelope.
    #[must_use]
    pub fn dispatch(&self, request: &Request) -> Value {
        self.dispatch_with_context(request, DispatchContext::empty())
    }

    /// Route `request` with daemon runtime context.
    #[must_use]
    pub fn dispatch_with_context(&self, request: &Request, context: DispatchContext<'_>) -> Value {
        let dispatch_start = Instant::now();
        let boot_to_dispatch_s = daemon_uptime_s();
        if request.op.trim().is_empty() {
            let mut response =
                error_envelope(ErrorKind::InvalidEnvelope, "op is required", json!({}));
            attach_runtime_timings(
                &mut response,
                boot_to_dispatch_s,
                dispatch_start.elapsed().as_secs_f64(),
                context.read_request_s.unwrap_or(0.0),
            );
            return response;
        }
        if !request.args.is_object() {
            let mut response = error_envelope(
                ErrorKind::InvalidEnvelope,
                "args must be an object",
                json!({}),
            );
            attach_runtime_timings(
                &mut response,
                boot_to_dispatch_s,
                dispatch_start.elapsed().as_secs_f64(),
                context.read_request_s.unwrap_or(0.0),
            );
            return response;
        }
        let Some(handler) = self.handlers.get(&request.op) else {
            if let Some(response) = crate::plugin::dispatch_registered_op(
                &request.op,
                &request.invocation_id,
                &request.args,
                context,
            ) {
                let mut response = match response {
                    Ok(response) => response,
                    Err(err) => error_envelope(err.wire_kind(), &err.to_string(), json!({})),
                };
                attach_runtime_timings(
                    &mut response,
                    boot_to_dispatch_s,
                    dispatch_start.elapsed().as_secs_f64(),
                    context.read_request_s.unwrap_or(0.0),
                );
                emit_dispatch_audit(request, &response, dispatch_start.elapsed().as_secs_f64());
                return response;
            }
            let mut response = error_envelope(
                ErrorKind::UnknownOp,
                &format!("unknown op: {}", request.op),
                json!({"op": request.op}),
            );
            attach_runtime_timings(
                &mut response,
                boot_to_dispatch_s,
                dispatch_start.elapsed().as_secs_f64(),
                context.read_request_s.unwrap_or(0.0),
            );
            return response;
        };
        let mut response = match handler(&request.args, context) {
            Ok(response) => response,
            Err(err) => error_envelope(err.wire_kind(), &err.to_string(), json!({})),
        };
        attach_runtime_timings(
            &mut response,
            boot_to_dispatch_s,
            dispatch_start.elapsed().as_secs_f64(),
            context.read_request_s.unwrap_or(0.0),
        );
        emit_dispatch_audit(request, &response, dispatch_start.elapsed().as_secs_f64());
        response
    }
}

/// Build the structured wire error envelope.
///
/// `warnings`/`timings` are always `[]`/`{}` at the builder. `details`
/// defaults to `{}` and `internal_error` responses receive a generated
/// `details.error_id` when the caller did not provide one.
#[must_use]
pub fn error_envelope(kind: ErrorKind, message: &str, details: Value) -> Value {
    let is_internal_error = kind == ErrorKind::InternalError;
    let kind_str = serde_json::to_value(kind).unwrap_or(Value::Null);
    let details = error_details(is_internal_error, details);
    json!({
        "success": false,
        "warnings": [],
        "timings": {},
        "error": {
            "kind": kind_str,
            "message": message,
            "details": details,
        },
    })
}

fn error_details(is_internal_error: bool, details: Value) -> Value {
    if !is_internal_error {
        return if details.is_null() {
            json!({})
        } else {
            details
        };
    }
    let mut details = match details {
        Value::Null => serde_json::Map::new(),
        Value::Object(details) => details,
        other => {
            let mut object = serde_json::Map::new();
            object.insert("value".to_owned(), other);
            object
        }
    };
    details
        .entry("error_id")
        .or_insert_with(|| Value::String(new_error_id()));
    Value::Object(details)
}

fn new_error_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

/// `api.runtime.ready` — binary readiness plus the three plane probes
/// (`control_plane` / `data_plane` / `mutation_gate`). Requires `layer_stack_root`.
fn op_runtime_ready(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = require_string(args, "layer_stack_root")?;
    let mut timings = serde_json::Map::new();
    let probes = vec![
        run_probe("control_plane", || probe_control_plane(&root), &mut timings),
        run_probe("data_plane", || Ok(probe_data_plane()), &mut timings),
        run_probe("mutation_gate", || Ok(probe_mutation_gate()), &mut timings),
    ];
    timings.insert(
        "runtime.ready.total_s".to_owned(),
        json!(total_start.elapsed().as_secs_f64()),
    );
    Ok(json!({
        "success": true,
        "ready": probes.iter().all(|probe| probe.get("status") == Some(&Value::String("ok".to_owned()))),
        "probes": probes,
        "daemon_pid": std::process::id(),
        "uptime_s": daemon_uptime_s(),
        "timings": Value::Object(timings),
    }))
}

/// `api.v1.cancel` — cancel one in-flight invocation id.
// Op handlers share the fallible dispatcher ABI even when this handler encodes
// invalid/missing ids as ordinary JSON response fields.
#[expect(
    clippy::unnecessary_wraps,
    reason = "dispatcher handlers share a fallible ABI"
)]
fn op_cancel(args: &Value, context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let invocation_id = args
        .get("invocation_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_owned();
    let (cancelled, cleanup_done) = context
        .invocation_registry
        .map_or((false, true), |registry| {
            let cancelled = registry.cancel(&invocation_id);
            let cleanup_done =
                !cancelled || registry.wait_for_cleanup(&invocation_id, Duration::from_secs(5));
            (cancelled, cleanup_done)
        });
    Ok(json!({
        "success": true,
        "invocation_id": invocation_id,
        "cancelled": cancelled,
        "already_done": !cancelled,
        "cleanup_done": cleanup_done,
    }))
}

/// `api.v1.heartbeat` — touch `last_seen` for the given invocation ids.
// Op handlers share the fallible dispatcher ABI even when this handler encodes
// invalid/missing ids as ordinary JSON response fields.
#[expect(
    clippy::unnecessary_wraps,
    reason = "dispatcher handlers share a fallible ABI"
)]
fn op_heartbeat(args: &Value, context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let invocation_ids: Vec<String> = args
        .get("invocation_ids")
        .and_then(Value::as_array)
        .map(|ids| {
            ids.iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default();
    let touched = context
        .invocation_registry
        .map_or(0, |registry| registry.heartbeat(&invocation_ids));
    Ok(json!({"success": true, "touched": touched}))
}

/// `api.v1.inflight_count` — count background daemon invocations for one agent.
// Op handlers share the fallible dispatcher ABI even when this handler encodes
// missing registry state as a zero count.
#[expect(
    clippy::unnecessary_wraps,
    reason = "dispatcher handlers share a fallible ABI"
)]
fn op_inflight_count(args: &Value, context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let agent_id = args
        .get("agent_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_owned();
    let count = context
        .invocation_registry
        .map_or(0, |registry| registry.count_by_agent(&agent_id));
    Ok(json!({"success": true, "agent_id": agent_id, "count": count}))
}

/// `api.layer_metrics` — summarize layer-stack storage + lease state for a root.
fn op_layer_metrics(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let stack = LayerStack::open(root.clone())?;
    let manifest = stack.read_active_manifest()?;
    let binding = read_workspace_binding(&root)?;
    Ok(json!({
        "success": true,
        "manifest_version": manifest.version,
        "manifest_depth": manifest.depth(),
        "active_leases": stack.active_lease_count(),
        "leased_layers": stack.leased_layers().len(),
        "layer_dirs": count_dirs(&root.join("layers"))?,
        "referenced_layers": manifest.layers.len(),
        "orphan_layer_count": 0,
        "missing_layer_count": 0,
        "orphan_layer_ids": [],
        "missing_layer_ids": [],
        "staging_dirs": count_dirs(&root.join("staging"))?,
        "storage_bytes": storage_bytes(&root)?,
        "workspace_bound": binding.is_some(),
        "workspace_root": binding.as_ref().map_or("", |binding| binding.workspace_root.as_str()),
        "base_root_hash": binding.as_ref().map_or("", |binding| binding.base_root_hash.as_str()),
        "occ_runtime_service_cache": occ_service_cache_snapshot(),
    }))
}

fn op_build_workspace_base(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let workspace_root = PathBuf::from(require_string(args, "workspace_root")?);
    let reset = args.get("reset").and_then(Value::as_bool).unwrap_or(false);
    if reset {
        crate::plugin::stop_services_for_layer_stack_root(&root.to_string_lossy())?;
    }
    let built = build_workspace_base(&root, &workspace_root, reset)?;
    let mut timings = timings_to_value_map(&built.timings);
    timings.insert(
        "api.workspace_base.total_s".to_owned(),
        json!(total_start.elapsed().as_secs_f64()),
    );
    let binding = binding_to_value(&built.binding)?;
    Ok(json!({
        "success": true,
        "created": true,
        "binding": binding,
        "timings": Value::Object(timings),
    }))
}

fn op_ensure_workspace_base(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let workspace_root = PathBuf::from(require_string(args, "workspace_root")?);
    let (binding, created) = ensure_workspace_base(&root, &workspace_root)?;
    let binding = binding_to_value(&binding)?;
    let timings = json!({
        "api.workspace_base.total_s": total_start.elapsed().as_secs_f64(),
    });
    Ok(json!({
        "success": true,
        "created": created,
        "binding": binding,
        "timings": timings,
    }))
}

fn op_commit_to_workspace(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let workspace_root = PathBuf::from(require_string(args, "workspace_root")?);
    let mut stack = LayerStack::open(root)?;
    let (manifest, commit_timings) = stack.commit_to_workspace(&workspace_root)?;
    let mut timings = timings_to_value_map(&commit_timings);
    timings.insert(
        "api.commit_to_workspace.total_s".to_owned(),
        json!(total_start.elapsed().as_secs_f64()),
    );
    Ok(json!({
        "success": true,
        "manifest_version": manifest.version,
        "timings": Value::Object(timings),
    }))
}

fn op_workspace_binding(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let binding = require_workspace_binding(&root)?;
    let binding = binding_to_value(&binding)?;
    Ok(json!({
        "success": true,
        "binding": binding,
    }))
}

fn run_probe<F>(name: &str, probe: F, timings: &mut serde_json::Map<String, Value>) -> Value
where
    F: FnOnce() -> Result<Value, DaemonError>,
{
    let start = Instant::now();
    let (status, details) = match probe() {
        Ok(details) => ("ok", details),
        Err(err) => (
            "down",
            json!({"error_type": error_type(&err), "error": err.to_string()}),
        ),
    };
    timings.insert(
        format!("runtime.ready.{name}_s"),
        json!(start.elapsed().as_secs_f64()),
    );
    json!({"name": name, "status": status, "details": details})
}

fn probe_control_plane(root: &str) -> Result<Value, DaemonError> {
    let binding = require_workspace_binding(root)?;
    let stack = LayerStack::open(PathBuf::from(root))?;
    let manifest = stack.read_active_manifest()?;
    Ok(json!({
        "workspace_root": binding.workspace_root,
        "manifest_version": manifest.version,
        "manifest_depth": manifest.depth(),
        "base_root_hash": binding.base_root_hash,
    }))
}

fn probe_data_plane() -> Value {
    json!({
        "handlers_services_ready": true,
        "shell_services_ready": true,
        "workspace_mount_mode": "private_namespace",
    })
}

fn probe_mutation_gate() -> Value {
    json!({
        "backend_ready": true,
        "backend_fields": ["layer_stack", "occ_service", "occ_client", "gitignore", "layer_stack_manager"],
        "occ_client_class": "OccClient",
    })
}

fn count_dirs(path: &Path) -> Result<usize, DaemonError> {
    if !path.exists() {
        return Ok(0);
    }
    let mut count = 0;
    for entry in std::fs::read_dir(path)? {
        if entry?.file_type()?.is_dir() {
            count += 1;
        }
    }
    Ok(count)
}

fn storage_bytes(path: &Path) -> Result<u64, DaemonError> {
    if !path.exists() {
        return Ok(0);
    }
    let mut total = 0;
    let mut stack = vec![path.to_path_buf()];
    while let Some(dir) = stack.pop() {
        for entry in std::fs::read_dir(dir)? {
            let entry = entry?;
            let meta = entry.metadata()?;
            if meta.is_dir() {
                stack.push(entry.path());
            } else if meta.is_file() {
                total += meta.len();
            }
        }
    }
    Ok(total)
}

fn attach_runtime_timings(
    response: &mut Value,
    boot_to_dispatch_s: f64,
    dispatch_s: f64,
    read_request_s: f64,
) {
    let Some(obj) = response.as_object_mut() else {
        return;
    };
    let timings = obj
        .entry("timings")
        .or_insert_with(|| Value::Object(serde_json::Map::new()));
    if let Value::Object(timings) = timings {
        timings.insert(
            "runtime.boot_to_dispatch_s".to_owned(),
            json!(boot_to_dispatch_s),
        );
        timings.insert("runtime.dispatch_s".to_owned(), json!(dispatch_s));
        timings.insert("runtime.read_request_s".to_owned(), json!(read_request_s));
    }
}

fn daemon_uptime_s() -> f64 {
    static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}

const fn error_type(err: &DaemonError) -> &'static str {
    match err {
        DaemonError::LayerStack(eos_layerstack::LayerStackError::WorkspaceBinding(_)) => {
            "WorkspaceBindingError"
        }
        DaemonError::LayerStack(eos_layerstack::LayerStackError::Manifest(_)) => {
            "ManifestConflictError"
        }
        DaemonError::Io(_) => "OSError",
        DaemonError::InvalidEnvelope(_) => "ValueError",
        _ => "RuntimeError",
    }
}

#[cfg(test)]
#[path = "../tests/dispatcher/mod.rs"]
mod tests;
