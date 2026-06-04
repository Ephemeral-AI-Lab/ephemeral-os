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
        table.register_builtin("api.v1.glob", crate::workspace_ops::op_glob);
        table.register_builtin("api.v1.grep", crate::workspace_ops::op_grep);
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
mod tests {
    use std::future;
    use std::sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    };
    use std::thread;
    use std::time::Duration;

    use eos_protocol::audit::Lane;
    use serde_json::json;

    use super::*;

    type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

    #[test]
    fn upperdir_tree_resource_timings_capture_bounded_payload() -> TestResult {
        let fixture = Fixture::new("upperdir_tree_stats")?;
        let upperdir = fixture.base.join("upperdir");
        std::fs::create_dir_all(upperdir.join("nested"))?;
        std::fs::write(upperdir.join("nested/payload.bin"), vec![7_u8; 4096])?;

        let manifest = LayerStack::open(fixture.root.clone())?.read_active_manifest()?;
        let mut timings = resource_timings(&manifest, 1);
        insert_tree_resource_timings(
            &mut timings,
            "resource.command_exec.upperdir",
            &TreeResourceStats::collect(&upperdir),
        );

        assert_eq!(
            timing_f64_value(&timings, "resource.command_exec.workspace_tree_bytes"),
            0.0
        );
        assert_eq!(
            timing_f64_value(&timings, "resource.command_exec.upperdir_tree_exists"),
            1.0
        );
        assert!(timing_f64_value(&timings, "resource.command_exec.upperdir_tree_bytes") >= 4096.0);
        assert_eq!(
            timing_f64_value(&timings, "resource.command_exec.upperdir_tree_truncated"),
            0.0
        );
        Ok(())
    }

    #[test]
    fn op_table_rejects_different_handler_collision() {
        #[expect(
            clippy::unnecessary_wraps,
            reason = "test handlers must match the dispatcher handler ABI"
        )]
        fn first_handler(
            _args: &Value,
            _context: DispatchContext<'_>,
        ) -> Result<Value, DaemonError> {
            Ok(json!({"handler": "first"}))
        }
        #[expect(
            clippy::unnecessary_wraps,
            reason = "test handlers must match the dispatcher handler ABI"
        )]
        fn second_handler(
            _args: &Value,
            _context: DispatchContext<'_>,
        ) -> Result<Value, DaemonError> {
            Ok(json!({"handler": "second"}))
        }

        let mut table = OpTable::default();
        assert!(table.register("api.test.collision", first_handler));
        assert!(table.register("api.test.collision", first_handler));
        assert!(!table.register("api.test.collision", second_handler));

        let response = table.dispatch(&Request {
            op: "api.test.collision".to_owned(),
            invocation_id: "collision-test".to_owned(),
            args: json!({}),
        });
        assert_eq!(response["handler"], "first");
    }

    #[test]
    fn builtin_table_routes_commit_to_workspace() {
        let response = OpTable::with_builtins().dispatch(&Request {
            op: "api.commit_to_workspace".to_owned(),
            invocation_id: "commit-to-workspace-route-test".to_owned(),
            args: json!({}),
        });

        assert_ne!(response["error"]["kind"], json!("unknown_op"));
        assert_eq!(response["error"]["kind"], json!("invalid_envelope"));
        assert!(response["error"]["message"]
            .as_str()
            .unwrap_or_default()
            .contains("layer_stack_root is required"));
    }

    #[test]
    fn dispatch_attaches_real_runtime_timings() {
        #[expect(
            clippy::unnecessary_wraps,
            reason = "test handlers must match the dispatcher handler ABI"
        )]
        fn slow_handler(
            _args: &Value,
            _context: DispatchContext<'_>,
        ) -> Result<Value, DaemonError> {
            std::thread::sleep(std::time::Duration::from_millis(2));
            Ok(json!({"success": true}))
        }

        let mut table = OpTable::default();
        assert!(table.register("api.test.slow", slow_handler));

        let response = table.dispatch_with_context(
            &Request {
                op: "api.test.slow".to_owned(),
                invocation_id: "timings-test".to_owned(),
                args: json!({}),
            },
            DispatchContext {
                invocation_registry: None,
                read_request_s: Some(0.125),
            },
        );

        assert_eq!(response["success"], json!(true));
        assert!(
            response["timings"]["runtime.boot_to_dispatch_s"]
                .as_f64()
                .unwrap_or_default()
                >= 0.0
        );
        assert!(
            response["timings"]["runtime.dispatch_s"]
                .as_f64()
                .unwrap_or_default()
                > 0.0
        );
        assert_eq!(response["timings"]["runtime.read_request_s"], json!(0.125));
    }

    #[tokio::test]
    async fn cancel_waits_for_bounded_cleanup() -> TestResult {
        let registry = Arc::new(InFlightRegistry::new(300.0, 30.0));
        let task = tokio::spawn(future::pending::<()>());
        registry.register(
            "cancel-target",
            task.abort_handle(),
            "agent-a",
            "api.v1.exec_command",
            true,
        );
        let cleanup_registry = Arc::clone(&registry);
        let cleanup_thread = thread::spawn(move || {
            thread::sleep(Duration::from_millis(20));
            cleanup_registry.deregister("cancel-target");
        });

        let response = OpTable::with_builtins().dispatch_with_context(
            &Request {
                op: "api.v1.cancel".to_owned(),
                invocation_id: "cancel-request".to_owned(),
                args: json!({"invocation_id": "cancel-target"}),
            },
            DispatchContext::with_invocation_registry(&registry),
        );

        cleanup_thread
            .join()
            .map_err(|_| "cleanup helper panicked")?;
        assert_eq!(response["cancelled"], json!(true));
        assert_eq!(response["already_done"], json!(false));
        assert_eq!(response["cleanup_done"], json!(true));
        match task.await {
            Ok(()) => Err("expected cancelled task".into()),
            Err(error) if error.is_cancelled() => Ok(()),
            Err(error) => Err(format!("expected cancellation, got {error}").into()),
        }
    }

    #[test]
    fn internal_error_envelope_adds_error_id() {
        let response = error_envelope(
            ErrorKind::InternalError,
            "daemon invocation failed",
            json!({"op": "api.test.failure"}),
        );

        assert_eq!(response["error"]["kind"], json!("internal_error"));
        assert_eq!(
            response["error"]["details"]["op"],
            json!("api.test.failure")
        );
        let Some(error_id) = response["error"]["details"]["error_id"].as_str() else {
            panic!("internal errors carry details.error_id");
        };
        assert_eq!(error_id.len(), 32);
        assert!(error_id.bytes().all(|byte| byte.is_ascii_hexdigit()));
        assert_eq!(error_id.as_bytes()[12], b'4');
        assert!(matches!(error_id.as_bytes()[16], b'8' | b'9' | b'a' | b'b'));
    }

    #[test]
    fn base_hashes_accept_opaque_dir_over_existing_directory() -> TestResult {
        let fixture = Fixture::new("opaque_base_hash")?;
        std::fs::create_dir_all(fixture.root.join("layers/B000001-base/opaque_dir"))?;
        std::fs::write(
            fixture.root.join("layers/B000001-base/opaque_dir/old.txt"),
            "old\n",
        )?;
        let manifest = LayerStack::open(fixture.root.clone())?.read_active_manifest()?;

        let hashes = base_hashes_for_snapshot(
            &fixture.root,
            &manifest,
            &[LayerChange::OpaqueDir {
                path: lp("opaque_dir")?,
            }],
        )?;

        assert_eq!(hashes, vec![(lp("opaque_dir")?, None)]);
        Ok(())
    }

    #[test]
    fn command_collect_completed_is_background_only_not_overlay_lifecycle() {
        let request = Request {
            op: "api.v1.command.collect_completed".to_owned(),
            invocation_id: "collect-completed".to_owned(),
            args: json!({"command_session_id": "cmd-1", "agent_id": "agent-1"}),
        };

        assert_eq!(
            background_event_kind(&request, &json!({"success": true})),
            Some(("background_tool.completed", "command_session"))
        );
        assert!(!uses_overlay_or_lease(
            &request.op,
            &json!({"success": true})
        ));
    }

    #[test]
    fn gated_stale_base_aborts_without_publish() -> TestResult {
        let fixture = Fixture::new("gated_stale")?;
        let old_hash = hash_bytes(b"# README\n");
        LayerStack::open(fixture.root.clone())?.publish_layer(&[LayerChange::Write {
            path: lp("README.md")?,
            content: b"# theirs\n".to_vec(),
        }])?;

        let result = transaction(&fixture)
            .revalidate_and_publish(&PreparedChangeset {
                snapshot_version: Some(1),
                path_groups: vec![publish_decision("README.md", Route::Gated, Some(old_hash))?],
                changes: vec![LayerChange::Write {
                    path: lp("README.md")?,
                    content: b"# mine\n".to_vec(),
                }],
                atomic: true,
            })
            .map_err(|conflict| format!("unexpected publish conflict: {conflict:?}"))?;

        assert_eq!(result.published_manifest_version, None);
        assert_eq!(result.files[0].status, OccStatus::AbortedVersion);
        assert_eq!(read_text(&fixture, "README.md")?, "# theirs\n");
        Ok(())
    }

    #[test]
    fn direct_route_ignores_stale_base_and_publishes() -> TestResult {
        let fixture = Fixture::new("direct_stale")?;
        LayerStack::open(fixture.root.clone())?.publish_layer(&[LayerChange::Write {
            path: lp("target/out.txt")?,
            content: b"theirs\n".to_vec(),
        }])?;

        let result = transaction(&fixture)
            .revalidate_and_publish(&PreparedChangeset {
                snapshot_version: Some(1),
                path_groups: vec![publish_decision(
                    "target/out.txt",
                    Route::Direct,
                    Some("stale".to_owned()),
                )?],
                changes: vec![LayerChange::Write {
                    path: lp("target/out.txt")?,
                    content: b"mine\n".to_vec(),
                }],
                atomic: true,
            })
            .map_err(|conflict| format!("unexpected publish conflict: {conflict:?}"))?;

        assert!(result.success());
        assert_eq!(result.files[0].status, OccStatus::Committed);
        assert_eq!(read_text(&fixture, "target/out.txt")?, "mine\n");
        Ok(())
    }

    #[test]
    fn atomic_mixed_validation_failure_drops_accepted_paths() -> TestResult {
        let fixture = Fixture::new("atomic_mixed")?;
        let old_hash = hash_bytes(b"# README\n");
        LayerStack::open(fixture.root.clone())?.publish_layer(&[LayerChange::Write {
            path: lp("README.md")?,
            content: b"# theirs\n".to_vec(),
        }])?;

        let result = transaction(&fixture)
            .revalidate_and_publish(&PreparedChangeset {
                snapshot_version: Some(1),
                path_groups: vec![
                    publish_decision("README.md", Route::Gated, Some(old_hash))?,
                    publish_decision("target/out.txt", Route::Direct, None)?,
                ],
                changes: vec![
                    LayerChange::Write {
                        path: lp("README.md")?,
                        content: b"# mine\n".to_vec(),
                    },
                    LayerChange::Write {
                        path: lp("target/out.txt")?,
                        content: b"ok\n".to_vec(),
                    },
                ],
                atomic: true,
            })
            .map_err(|conflict| format!("unexpected publish conflict: {conflict:?}"))?;

        assert_eq!(result.published_manifest_version, None);
        assert_eq!(result.files[0].status, OccStatus::AbortedVersion);
        assert_eq!(result.files[1].status, OccStatus::Dropped);
        assert_eq!(read_text(&fixture, "README.md")?, "# theirs\n");
        assert!(
            !LayerStack::open(fixture.root.clone())?
                .read_bytes("target/out.txt")?
                .1
        );
        Ok(())
    }

    #[test]
    fn root_gitignore_routes_target_as_direct() -> TestResult {
        let fixture = Fixture::new_with_gitignore("gitignore_direct", "target/\n*.pyc\n")?;
        let provider = LayerStackRouteProvider {
            root: fixture.root.clone(),
        };

        assert!(provider.is_ignored(&lp("target/out.txt")?)?);
        assert!(provider.is_ignored(&lp("pkg/cache.pyc")?)?);
        assert!(!provider.is_ignored(&lp("src/main.rs")?)?);
        Ok(())
    }

    #[test]
    fn occ_route_metrics_count_gated_and_direct_paths() -> TestResult {
        let fixture = Fixture::new_with_gitignore("route_metrics", "target/\n*.pyc\n")?;
        let metrics = occ_route_metrics(
            &fixture.root,
            &[
                LayerChange::Write {
                    path: lp("src/main.rs")?,
                    content: b"tracked".to_vec(),
                },
                LayerChange::Write {
                    path: lp("target/out.txt")?,
                    content: b"direct".to_vec(),
                },
                LayerChange::Write {
                    path: lp("pkg/cache.pyc")?,
                    content: b"direct".to_vec(),
                },
                LayerChange::Write {
                    path: lp(".git/config")?,
                    content: b"drop".to_vec(),
                },
            ],
        )?;

        assert_eq!(metrics.gated_path_count, 1);
        assert_eq!(metrics.direct_path_count, 2);
        Ok(())
    }

    fn route_provider(fixture: &Fixture) -> LayerStackRouteProvider {
        LayerStackRouteProvider {
            root: fixture.root.clone(),
        }
    }

    // N2 (HIGH): a no-slash dir-only pattern is anchored at *any* depth, so a
    // file under `frontend/node_modules/` routes DIRECT — the most common
    // misroute the old root-anchored prefix check produced.
    #[test]
    fn dir_only_pattern_matches_at_any_depth() -> TestResult {
        let fixture = Fixture::new_with_gitignore("n2_dir_only", "node_modules/\n")?;
        let provider = route_provider(&fixture);
        assert!(provider.is_ignored(&lp("frontend/node_modules/index.js")?)?);
        assert!(provider.is_ignored(&lp("node_modules/index.js")?)?);
        assert!(!provider.is_ignored(&lp("frontend/src/index.js")?)?);
        Ok(())
    }

    // N3 (HIGH, data-loss): `*` must not cross `/`. `logs/*.log` does NOT match
    // `logs/sub/x.log`, so it routes GATED (base-hash validated) — not
    // DIRECT-then-silently-clobber as the old `wildcard_match` allowed.
    #[test]
    fn star_does_not_cross_slash() -> TestResult {
        let fixture = Fixture::new_with_gitignore("n3_star_slash", "logs/*.log\n")?;
        let provider = route_provider(&fixture);
        assert!(provider.is_ignored(&lp("logs/app.log")?)?);
        assert!(!provider.is_ignored(&lp("logs/sub/x.log")?)?);
        Ok(())
    }

    // Nested `.gitignore` is scoped to its own subtree.
    #[test]
    fn nested_gitignore_is_scoped_to_its_subtree() -> TestResult {
        let fixture = Fixture::new_with_gitignores("nested", &[("frontend", "dist/\n")])?;
        let provider = route_provider(&fixture);
        assert!(provider.is_ignored(&lp("frontend/dist/bundle.js")?)?);
        assert!(!provider.is_ignored(&lp("dist/bundle.js")?)?);
        Ok(())
    }

    // `**` matches across path segments.
    #[test]
    fn double_star_matches_across_segments() -> TestResult {
        let fixture = Fixture::new_with_gitignore("double_star", "**/build/\n")?;
        let provider = route_provider(&fixture);
        assert!(provider.is_ignored(&lp("a/b/build/out.o")?)?);
        assert!(provider.is_ignored(&lp("build/out.o")?)?);
        assert!(!provider.is_ignored(&lp("a/b/builder.rs")?)?);
        Ok(())
    }

    // `!` re-includes within a non-sealed directory.
    #[test]
    fn bang_re_includes_in_unsealed_dir() -> TestResult {
        let fixture = Fixture::new_with_gitignore("bang", "*.log\n!keep.log\n")?;
        let provider = route_provider(&fixture);
        assert!(provider.is_ignored(&lp("other.log")?)?);
        assert!(!provider.is_ignored(&lp("keep.log")?)?);
        Ok(())
    }

    // Directory seal: an excluded ancestor dir seals its subtree — a deeper `!`
    // cannot rescue contents under it (Git semantics).
    #[test]
    fn excluded_dir_seals_against_deeper_reinclude() -> TestResult {
        let fixture =
            Fixture::new_with_gitignores("seal", &[("", "build/\n"), ("build", "!keep.txt\n")])?;
        let provider = route_provider(&fixture);
        assert!(provider.is_ignored(&lp("build/keep.txt")?)?);
        Ok(())
    }

    // Telemetry shares the one routine, so counts equal the route decision for
    // the same inputs (including the N2/N3/nested/seal cases above).
    #[test]
    fn occ_route_metrics_match_route_decision() -> TestResult {
        let fixture = Fixture::new_with_gitignores(
            "metrics_parity",
            &[
                ("", "node_modules/\nlogs/*.log\nbuild/\n"),
                ("build", "!keep.txt\n"),
            ],
        )?;
        let provider = route_provider(&fixture);
        let paths = [
            "frontend/node_modules/index.js", // DIRECT (N2 dir-only any depth)
            "logs/sub/x.log",                 // GATED  (N3 star not crossing /)
            "logs/app.log",                   // DIRECT
            "build/keep.txt",                 // DIRECT (seal beats deeper !)
            "src/main.rs",                    // GATED
            ".git/config",                    // skipped by metrics
        ];
        let mut expected_direct = 0;
        let mut expected_gated = 0;
        for path in paths {
            if path == ".git/config" {
                continue;
            }
            if provider.is_ignored(&lp(path)?)? {
                expected_direct += 1;
            } else {
                expected_gated += 1;
            }
        }
        let changes: Vec<LayerChange> = paths
            .iter()
            .map(|path| {
                Ok(LayerChange::Write {
                    path: lp(path)?,
                    content: b"x".to_vec(),
                })
            })
            .collect::<TestResult<_>>()?;
        let metrics = occ_route_metrics(&fixture.root, &changes)?;
        assert_eq!(metrics.direct_path_count, expected_direct);
        assert_eq!(metrics.gated_path_count, expected_gated);
        assert_eq!(expected_direct, 3);
        assert_eq!(expected_gated, 2);
        Ok(())
    }

    // Overlay/layerstack composition: a `.gitignore` published into an *upper*
    // layer (the base layer carries none) is resolved through the active merged
    // manifest — the same newest-layer-wins, whiteout-aware view the overlay
    // mount projects. Proves the oracle reads `.gitignore` via `read_bytes`/
    // `MergedView` across layers, not just from a single seeded layer.
    #[test]
    fn gitignore_resolves_through_published_upper_layer() -> TestResult {
        let fixture = Fixture::new("cross_layer")?;
        LayerStack::open(fixture.root.clone())?.publish_layer(&[
            LayerChange::Write {
                path: lp(".gitignore")?,
                content: b"node_modules/\n".to_vec(),
            },
            LayerChange::Write {
                path: lp("frontend/.gitignore")?,
                content: b"dist/\n".to_vec(),
            },
        ])?;
        let provider = route_provider(&fixture);
        // Root rule from the upper layer, matched at depth via the seal.
        assert!(provider.is_ignored(&lp("frontend/node_modules/index.js")?)?);
        // Nested rule, also published into the upper layer.
        assert!(provider.is_ignored(&lp("frontend/dist/bundle.js")?)?);
        assert!(!provider.is_ignored(&lp("src/main.rs")?)?);
        Ok(())
    }

    // Regression (double-strip on prefix replay, data-loss-class): a per-level
    // matcher for dir `D` must not strip `D` from a path whose next component
    // repeats `D`'s name. The caller already makes the path relative to `D`, so
    // the matcher must be rooted at `.` — `GitignoreBuilder::new(D)` would strip
    // `D` a SECOND time (raw byte prefix), turning `a/x` into `x` and matching an
    // anchored `/x`. Ground truth below is `git check-ignore --no-index`.
    #[test]
    fn nested_anchored_pattern_not_double_stripped_on_prefix_replay() -> TestResult {
        let fixture = Fixture::new_with_gitignores(
            "prefix_replay",
            &[("a", "/x\n/b\n"), ("build", "/build/x\n")],
        )?;
        let provider = route_provider(&fixture);
        // `/x` anchored at `a/` matches `a/x` (DIRECT) but NOT `a/a/x` — routing
        // the tracked `a/a/x` DIRECT would bypass the gate and silently clobber.
        assert!(provider.is_ignored(&lp("a/x")?)?);
        assert!(!provider.is_ignored(&lp("a/a/x")?)?);
        // Seal variant: `/b` seals `a/b`'s subtree, but `a/a/b` is not the
        // anchored `a/b`, so its whole subtree must stay GATED.
        assert!(provider.is_ignored(&lp("a/b/file.txt")?)?);
        assert!(!provider.is_ignored(&lp("a/a/b/file.txt")?)?);
        // Opposite (false-GATED) direction: `/build/x` anchored at `build/` DOES
        // match `build/build/x`; the old double-strip dropped it to `x` and missed.
        assert!(provider.is_ignored(&lp("build/build/x")?)?);
        assert!(!provider.is_ignored(&lp("build/x")?)?);
        Ok(())
    }

    #[test]
    fn audit_pull_reads_shared_daemon_ring() -> TestResult {
        let marker = format!("phase3t-audit-test-{}", unique_suffix());
        let after_seq = audit_after_seq()?;
        crate::audit_buffer::safe_emit(
            json!({"type": marker, "payload": {"source": "unit-test"}}),
            Lane::Normal,
        );

        let pulled = op_audit_pull(
            &json!({"after_seq": after_seq, "limit": 128}),
            DispatchContext::empty(),
        )?;

        let events = pulled["events"].as_array().ok_or("events array")?;
        assert!(events
            .iter()
            .any(|event| event["type"].as_str() == Some(marker.as_str())));
        Ok(())
    }

    #[test]
    fn auto_squash_audit_emits_triggered_and_completed() -> TestResult {
        let fixture = Fixture::new("auto_squash_completed")?;
        let manifest = LayerStack::open(fixture.root.clone())?.read_active_manifest()?;
        let expected_hash = eos_protocol::manifest_root_hash(&manifest);
        let invocation_id = format!("autosquash-completed-{}", unique_suffix());
        let request = Request {
            op: "api.v1.write_file".to_owned(),
            invocation_id: invocation_id.clone(),
            args: json!({"layer_stack_root": &fixture.root}),
        };
        let response = json!({
            "timings": {
                "layer_stack.auto_squash.depth_before": 101.0,
                "layer_stack.auto_squash.depth_after": 3.0,
                "layer_stack.auto_squash.total_s": 0.25,
                "layer_stack.auto_squash.manifest_version": i64_to_f64_saturating(manifest.version),
            }
        });
        let after_seq = audit_after_seq()?;

        emit_auto_squash_audit(&request, &response);

        let events = layer_stack_events_after(after_seq, &invocation_id)?;
        assert_eq!(
            event_types(&events),
            vec![
                "layer_stack.squash_triggered",
                "layer_stack.squash_completed"
            ]
        );
        assert_eq!(
            events[0]["payload"]["layer_stack"]["squash_trigger_reason"],
            "post_publish_depth"
        );
        assert_eq!(
            events[0]["payload"]["layer_stack"]["squash_input_layers"],
            101
        );
        assert_eq!(
            events[1]["payload"]["layer_stack"]["squash_result_layers"],
            3
        );
        assert_eq!(
            events[1]["payload"]["layer_stack"]["manifest_root_hash"],
            expected_hash
        );
        Ok(())
    }

    #[test]
    fn auto_squash_audit_emits_triggered_and_failed_for_race() -> TestResult {
        let invocation_id = format!("autosquash-raced-{}", unique_suffix());
        let request = Request {
            op: "api.v1.write_file".to_owned(),
            invocation_id: invocation_id.clone(),
            args: json!({}),
        };
        let response = json!({
            "timings": {
                "layer_stack.auto_squash.depth_before": 102.0,
                "layer_stack.auto_squash.total_s": 0.10,
                "layer_stack.auto_squash.raced": 1.0,
            }
        });
        let after_seq = audit_after_seq()?;

        emit_auto_squash_audit(&request, &response);

        let events = layer_stack_events_after(after_seq, &invocation_id)?;
        assert_eq!(
            event_types(&events),
            vec!["layer_stack.squash_triggered", "layer_stack.squash_failed"]
        );
        assert_eq!(
            events[1]["payload"]["layer_stack"]["squash_failure_kind"],
            "raced_or_plan_aborted"
        );
        assert_eq!(
            events[1]["payload"]["layer_stack"]["squash_trigger_reason"],
            "post_publish_depth"
        );
        Ok(())
    }

    #[test]
    fn occ_service_cache_is_bounded_lru() -> TestResult {
        let mut cache = OccServiceCache::default();
        let base = std::env::temp_dir().join(format!("eosd-occ-cache-{}", unique_suffix()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base)?;

        let first = base.join("root-000");
        for index in 0..=OCC_SERVICE_CACHE_MAX {
            let root = base.join(format!("root-{index:03}"));
            std::fs::create_dir_all(&root)?;
            let transaction = LayerStackCommitTransaction { root: root.clone() };
            let service = Arc::new(OccService::new(CommitQueue::new(transaction))?);
            let lookup = cache.insert_or_get(normalize_root_key(&root), service, 0.0);
            assert!(lookup.cache_created);
        }

        assert_eq!(cache.entries.len(), OCC_SERVICE_CACHE_MAX);
        assert_eq!(cache.stats.evictions_total, 1);

        let transaction = LayerStackCommitTransaction {
            root: first.clone(),
        };
        let service = Arc::new(OccService::new(CommitQueue::new(transaction))?);
        let recreated = cache.insert_or_get(normalize_root_key(&first), service, 0.0);
        assert!(!recreated.cache_hit);
        assert!(recreated.cache_created);
        assert_eq!(recreated.evicted_count, 1);

        let _ = std::fs::remove_dir_all(base);
        Ok(())
    }

    fn unique_suffix() -> String {
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        format!(
            "{}-{}",
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        )
    }

    fn transaction(fixture: &Fixture) -> LayerStackCommitTransaction {
        LayerStackCommitTransaction {
            root: fixture.root.clone(),
        }
    }

    fn publish_decision(
        path: &str,
        route: Route,
        base_hash: Option<String>,
    ) -> TestResult<eos_occ::PublishDecision> {
        Ok(eos_occ::PublishDecision {
            path: lp(path)?,
            route,
            base_hash,
            message: None,
        })
    }

    fn lp(path: &str) -> TestResult<LayerPath> {
        Ok(LayerPath::parse(path)?)
    }

    fn read_text(fixture: &Fixture, path: &str) -> TestResult<String> {
        Ok(LayerStack::open(fixture.root.clone())?.read_text(path)?.0)
    }

    fn timing_f64_value(timings: &serde_json::Map<String, Value>, key: &str) -> f64 {
        timings.get(key).and_then(Value::as_f64).unwrap_or(0.0)
    }

    fn audit_after_seq() -> TestResult<i64> {
        let snapshot = op_audit_snapshot(&json!({}), DispatchContext::empty())?;
        Ok(snapshot["snapshot"]["daemon"]["next_seq"]
            .as_i64()
            .unwrap_or(0)
            - 1)
    }

    fn layer_stack_events_after(after_seq: i64, invocation_id: &str) -> TestResult<Vec<Value>> {
        let pulled = op_audit_pull(
            &json!({"after_seq": after_seq, "limit": 128}),
            DispatchContext::empty(),
        )?;
        Ok(pulled["events"]
            .as_array()
            .ok_or("events array")?
            .iter()
            .filter(|event| {
                event["payload"]["layer_stack"]["operation_id"].as_str() == Some(invocation_id)
            })
            .cloned()
            .collect())
    }

    fn event_types(events: &[Value]) -> Vec<&str> {
        events
            .iter()
            .filter_map(|event| event["type"].as_str())
            .collect()
    }

    struct Fixture {
        base: PathBuf,
        root: PathBuf,
    }

    impl Fixture {
        fn new(label: &str) -> TestResult<Self> {
            Self::new_with_gitignores(label, &[])
        }

        fn new_with_gitignore(label: &str, gitignore: &str) -> TestResult<Self> {
            let seeds = if gitignore.is_empty() {
                Vec::new()
            } else {
                vec![("", gitignore)]
            };
            Self::new_with_gitignores(label, &seeds)
        }

        /// Seed one base layer with a `.gitignore` per `(dir, contents)` entry
        /// (`""` = workspace root) so nested / depth-sensitive routing is testable.
        fn new_with_gitignores(label: &str, gitignores: &[(&str, &str)]) -> TestResult<Self> {
            static COUNTER: AtomicU64 = AtomicU64::new(0);
            let base = std::env::temp_dir().join(format!(
                "eosd-occ-{label}-{}-{}",
                std::process::id(),
                COUNTER.fetch_add(1, Ordering::Relaxed)
            ));
            let _ = std::fs::remove_dir_all(&base);
            let root = base.join("layer-stack");
            let layer = root.join("layers").join("B000001-base");
            std::fs::create_dir_all(&layer)?;
            std::fs::create_dir_all(root.join("staging"))?;
            std::fs::write(layer.join("README.md"), "# README\n")?;
            for (dir, contents) in gitignores {
                let target = if dir.is_empty() {
                    layer.join(".gitignore")
                } else {
                    layer.join(dir).join(".gitignore")
                };
                if let Some(parent) = target.parent() {
                    std::fs::create_dir_all(parent)?;
                }
                std::fs::write(target, contents)?;
            }
            std::fs::write(
                root.join("manifest.json"),
                serde_json::to_string_pretty(&json!({
                    "schema_version": 1,
                    "version": 1,
                    "layers": [{"layer_id": "B000001-base", "path": "layers/B000001-base"}],
                }))?,
            )?;
            Ok(Self { base, root })
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.base);
        }
    }
}
