//! Op routing: the OP_TABLE, envelope validation, and the per-op handlers.
//!
//! The daemon decodes one [`eos_protocol::Request`] and routes `op` through the
//! [`OpTable`]. Handlers return a JSON `Value` response; a failure becomes the
//! structured error envelope ([`error_envelope`]) keyed by an
//! [`eos_protocol::ErrorKind`]. There is NO `ping` op — liveness is
//! `api.v1.heartbeat`, readiness is `api.runtime.ready`.
//!
//! Only the daemon-owned ops this phase wires are declared here:
//! `api.runtime.ready` (probes control_plane/data_plane/mutation_gate),
//! `api.v1.heartbeat`, `api.layer_metrics`, `api.audit.{pull,snapshot,reset_floor}`
//! (floor-reset gated by [`AUDIT_ALLOW_FLOOR_RESET_ENV`]). The full op table
//! (workspace-tool, isolated-workspace, plugin, layer-stack control) folds in at
//! port time through the same routing.
//! `// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:60-160 — dispatch_envelope_async`
//! `// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:404-449 — _register_builtin_operations / OP_TABLE`

use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
use std::time::Instant;

use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use eos_layerstack::{
    build_workspace_base, ensure_workspace_base, read_workspace_binding, require_workspace_binding,
    LayerStack, MergedView, WorkspaceBinding, AUTO_SQUASH_MAX_DEPTH,
};
use eos_occ::{
    ChangesetResult, CommitQueue, CommitTransactionPort, FileResult, OccRouteProvider, OccService,
    OccStatus, PreparedChangeset, PublishConflict, Route,
};
use eos_overlay::{allocate_overlay_writable_dirs, capture_upperdir, overlay_writable_root};
use eos_protocol::{
    apply_search_replace,
    audit::{build_event, Lane},
    models::{SearchReplaceEdit, MAX_READ_BYTES},
    ErrorKind, Intent, LayerChange, LayerPath, Manifest, Request, SearchReplaceError,
};
use eos_runner::{RunMode, RunRequest, RunResult, ToolCall, WorkspaceRoot};

use crate::error::DaemonError;
use crate::invocation_registry::InFlightRegistry;

/// Env gate for `api.audit.reset_floor` (must be `"true"`).
/// `// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:404 — EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET`
pub const AUDIT_ALLOW_FLOOR_RESET_ENV: &str = "EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET";

/// A synchronous op handler: decoded args -> response value.
///
/// The Python handlers are a mix of sync + async; the Rust dispatcher resolves
/// that at the call site. The daemon keeps the routing surface explicit here
/// and lets command/file/isolated handlers own their runtime details.
/// `// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:37 — Handler = Callable[[dict], Any]`
type Handler = for<'ctx> fn(&Value, DispatchContext<'ctx>) -> Result<Value, DaemonError>;

/// Per-dispatch daemon services used by handlers that need runtime state.
#[derive(Clone, Copy, Default)]
pub struct DispatchContext<'ctx> {
    invocation_registry: Option<&'ctx InFlightRegistry>,
}

impl<'ctx> DispatchContext<'ctx> {
    /// Empty context for direct unit dispatch.
    pub fn empty() -> Self {
        Self {
            invocation_registry: None,
        }
    }

    /// Context carrying the server's invocation registry.
    pub fn with_invocation_registry(invocation_registry: &'ctx InFlightRegistry) -> Self {
        Self {
            invocation_registry: Some(invocation_registry),
        }
    }
}

/// The op routing table. Re-registering the SAME handler under an op is a no-op;
/// a DIFFERENT handler under a claimed op is rejected so peer collisions surface.
/// `// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:42-57 — register_op + OP_TABLE`
#[derive(Clone, Default)]
pub struct OpTable {
    handlers: HashMap<String, Handler>,
}

impl OpTable {
    /// Build the table pre-populated with the daemon-owned builtin ops this
    /// phase wires (NO `ping`).
    // PORT backend/src/sandbox/daemon/rpc/dispatcher.py:404-449 — _register_builtin_operations
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
        table.register_builtin("api.workspace_binding", op_workspace_binding);
        table.register_builtin("api.audit.pull", op_audit_pull);
        table.register_builtin("api.audit.snapshot", op_audit_snapshot);
        table.register_builtin("api.audit.reset_floor", op_audit_reset_floor);
        table.register_builtin("api.read_file", op_read_file);
        table.register_builtin("api.v1.read_file", op_read_file);
        table.register_builtin("api.write_file", op_write_file);
        table.register_builtin("api.v1.write_file", op_write_file);
        table.register_builtin("api.edit_file", op_edit_file);
        table.register_builtin("api.v1.edit_file", op_edit_file);
        table.register_builtin("api.glob", op_glob);
        table.register_builtin("api.v1.glob", op_glob);
        table.register_builtin("api.grep", op_grep);
        table.register_builtin("api.v1.grep", op_grep);
        table.register_builtin("api.v1.shell", op_shell);
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
        table.register_builtin("api.v1.pty.write_stdin", crate::command::op_pty_write_stdin);
        table.register_builtin("api.v1.pty.progress", crate::command::op_pty_progress);
        table.register_builtin("api.v1.pty.cancel", crate::command::op_pty_cancel);
        table.register_builtin(
            "api.v1.pty.collect_completed",
            crate::command::op_pty_collect_completed,
        );
        table.register_builtin(
            "api.v1.pty_session_count",
            crate::command::op_pty_session_count,
        );
        table
    }

    /// Register `handler` under `op`.
    ///
    /// Returns `true` when the handler was inserted or already registered.
    /// Returns `false` when `op` is already claimed by a different handler,
    /// leaving the original route intact.
    // PORT backend/src/sandbox/daemon/rpc/dispatcher.py:42-57 — register_op (collision reject)
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
    // PORT backend/src/sandbox/daemon/rpc/dispatcher.py:60-160 — dispatch_envelope_async core
    pub fn dispatch(&self, request: &Request) -> Value {
        self.dispatch_with_context(request, DispatchContext::empty())
    }

    /// Route `request` with daemon runtime context.
    pub fn dispatch_with_context(&self, request: &Request, context: DispatchContext<'_>) -> Value {
        let dispatch_start = Instant::now();
        if request.op.trim().is_empty() {
            return error_envelope(ErrorKind::InvalidEnvelope, "op is required", json!({}));
        }
        if !request.args.is_object() {
            return error_envelope(
                ErrorKind::InvalidEnvelope,
                "args must be an object",
                json!({}),
            );
        }
        let Some(handler) = self.handlers.get(&request.op) else {
            return error_envelope(
                ErrorKind::UnknownOp,
                &format!("unknown op: {}", request.op),
                json!({"op": request.op}),
            );
        };
        let response = match handler(&request.args, context) {
            Ok(mut response) => {
                attach_runtime_timings(&mut response);
                response
            }
            Err(err) => error_envelope(err.wire_kind(), &err.to_string(), json!({})),
        };
        emit_dispatch_audit(request, &response, dispatch_start.elapsed().as_secs_f64());
        response
    }
}

/// Build the structured wire error envelope.
///
/// `warnings`/`timings` are always `[]`/`{}` at the builder; `details` defaults
/// to `{}`.
/// `// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:215-229 — _error_envelope`
pub fn error_envelope(kind: ErrorKind, message: &str, details: Value) -> Value {
    let kind_str = serde_json::to_value(kind).unwrap_or(Value::Null);
    json!({
        "success": false,
        "warnings": [],
        "timings": {},
        "error": {
            "kind": kind_str,
            "message": message,
            "details": if details.is_null() { json!({}) } else { details },
        },
    })
}

/// `api.runtime.ready` — binary readiness plus the three plane probes
/// (control_plane / data_plane / mutation_gate). Requires `layer_stack_root`.
// PORT backend/src/sandbox/daemon/builtin_operations.py:176-198 — runtime_ready: probe control_plane/data_plane/mutation_gate
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
// PORT backend/src/sandbox/daemon/builtin_operations.py:94-110 — cancel: registry.cancel_task(id), wait cleanup
fn op_cancel(args: &Value, context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let invocation_id = args
        .get("invocation_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_owned();
    let cancelled = context
        .invocation_registry
        .is_some_and(|registry| registry.cancel(&invocation_id));
    Ok(json!({
        "success": true,
        "invocation_id": invocation_id,
        "cancelled": cancelled,
        "already_done": !cancelled,
        "cleanup_done": !cancelled,
    }))
}

/// `api.v1.heartbeat` — touch `last_seen` for the given invocation ids.
// PORT backend/src/sandbox/daemon/builtin_operations.py:113-117 — heartbeat: registry.heartbeat(ids) -> {success, touched}
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
// PORT backend/src/sandbox/daemon/builtin_operations.py:120-123 — inflight_count
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
// PORT backend/src/sandbox/daemon/builtin_operations.py:132-170 — layer_metrics
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
        "workspace_root": binding.as_ref().map(|binding| binding.workspace_root.as_str()).unwrap_or(""),
        "base_root_hash": binding.as_ref().map(|binding| binding.base_root_hash.as_str()).unwrap_or(""),
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

fn op_workspace_binding(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let binding = require_workspace_binding(&root)?;
    let binding = binding_to_value(&binding)?;
    Ok(json!({
        "success": true,
        "binding": binding,
    }))
}

/// `api.audit.pull` — drain ring events after a cursor (backs the pull API).
// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:413-421 — _audit_pull_handler
fn op_audit_pull(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let after_seq = args.get("after_seq").and_then(Value::as_i64).unwrap_or(-1);
    let limit = args.get("limit").and_then(Value::as_u64).unwrap_or(1000) as usize;
    let mut response = crate::audit_buffer::global_audit_buffer().pull(after_seq, limit);
    response["success"] = Value::Bool(true);
    Ok(response)
}

/// `api.audit.snapshot` — ring buffer + snapshot blocks, no events.
// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:423-428 — _audit_snapshot_handler
fn op_audit_snapshot(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let _ = args;
    let mut response = crate::audit_buffer::global_audit_buffer().snapshot();
    response["success"] = Value::Bool(true);
    Ok(response)
}

/// `api.audit.reset_floor` — gated behind [`AUDIT_ALLOW_FLOOR_RESET_ENV`].
// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:430-438 — _audit_reset_floor_handler (env gate -> forbidden)
fn op_audit_reset_floor(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let _ = args;
    if std::env::var(AUDIT_ALLOW_FLOOR_RESET_ENV)
        .map(|raw| raw == "true")
        .unwrap_or(false)
    {
        Ok(json!({"success": true, "reset": true}))
    } else {
        Err(DaemonError::Forbidden(
            "audit floor reset is disabled".to_owned(),
        ))
    }
}

/// `api.v1.read_file` — direct LayerStack read path.
// PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:300-317 — _read_file_from_layer_stack
fn op_read_file(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let raw_path = require_string(args, "path")?;
    let binding = require_workspace_binding(&root)?;
    let layer_path = if raw_path.starts_with('/') {
        binding.layer_path_from_absolute(&raw_path)?
    } else {
        binding.layer_path_from_relative(&raw_path)?
    };
    let stack = LayerStack::open(root)?;
    let read_start = Instant::now();
    let (bytes, exists) = stack.read_bytes(&layer_path)?;
    let content = if exists {
        let bytes = bytes.unwrap_or_default();
        if bytes.len() > MAX_READ_BYTES {
            return Err(DaemonError::InvalidEnvelope(format!(
                "file too large: {} > {} bytes",
                bytes.len(),
                MAX_READ_BYTES
            )));
        }
        String::from_utf8_lossy(&bytes).into_owned()
    } else {
        String::new()
    };
    let manifest = stack.read_active_manifest()?;
    Ok(json!({
        "success": true,
        "workspace": "ephemeral",
        "content": content,
        "exists": exists,
        "encoding": "utf-8",
        "timings": {
            "resource.command_exec.changed_path_count": 0.0,
            "resource.layer_stack.manifest_depth": manifest.depth() as f64,
            "resource.layer_stack.manifest_path_count": manifest.depth() as f64,
            "resource.command_exec.run_dir_tree_exists": 0.0,
            "resource.command_exec.run_dir_tree_bytes": 0.0,
            "resource.command_exec.run_dir_tree_file_count": 0.0,
            "resource.command_exec.run_dir_tree_dir_count": 0.0,
            "resource.command_exec.run_dir_tree_entry_count": 0.0,
            "resource.command_exec.run_dir_tree_truncated": 0.0,
            "resource.command_exec.workspace_tree_exists": 0.0,
            "resource.command_exec.workspace_tree_bytes": 0.0,
            "resource.command_exec.workspace_tree_file_count": 0.0,
            "resource.command_exec.workspace_tree_dir_count": 0.0,
            "resource.command_exec.workspace_tree_entry_count": 0.0,
            "resource.command_exec.workspace_tree_truncated": 0.0,
            "resource.command_exec.upperdir_tree_exists": 0.0,
            "resource.command_exec.upperdir_tree_bytes": 0.0,
            "resource.command_exec.upperdir_tree_file_count": 0.0,
            "resource.command_exec.upperdir_tree_dir_count": 0.0,
            "resource.command_exec.upperdir_tree_entry_count": 0.0,
            "resource.command_exec.upperdir_tree_truncated": 0.0,
            "api.read.layer_stack_read_s": read_start.elapsed().as_secs_f64(),
            "api.read.total_s": total_start.elapsed().as_secs_f64(),
        },
    }))
}

/// `api.v1.write_file` — direct LayerStack write publish path.
// PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:321-363 — _write_file_to_layer_stack
fn op_write_file(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let layer_path = bound_layer_path(&root, args)?;
    let content = args
        .get("content")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .as_bytes()
        .to_vec();
    let stack = LayerStack::open(root.clone())?;

    if !args
        .get("overwrite")
        .and_then(Value::as_bool)
        .unwrap_or(true)
    {
        let (_current, exists) = stack.read_text(&layer_path)?;
        if exists {
            let manifest = stack.read_active_manifest()?;
            return Ok(guarded_conflict_response(
                "write",
                &layer_path,
                "rejected",
                "create_only_existing",
                "file already exists",
                resource_timings(&manifest, 0),
                total_start,
            ));
        }
    }
    let manifest = stack.read_active_manifest()?;
    let (base_bytes, base_exists) = stack.read_bytes(&layer_path)?;
    let base_hash = hash_current(base_bytes.as_deref(), base_exists);

    drop(stack);
    let occ_start = Instant::now();
    let path = LayerPath::parse(&layer_path).map_err(eos_layerstack::LayerStackError::from)?;
    let result = apply_occ_changeset(
        &root,
        Some(manifest.version as u64),
        &[LayerChange::Write {
            path: path.clone(),
            content,
        }],
        &[(path, base_hash)],
    )?;
    let manifest = LayerStack::open(root)?.read_active_manifest()?;
    let mut timings = resource_timings(&manifest, published_file_count(&result));
    timings.insert(
        "api.write.occ_apply_s".to_owned(),
        json!(occ_start.elapsed().as_secs_f64()),
    );
    Ok(guarded_changeset_response(
        "write",
        &result,
        timings,
        total_start,
        None,
    ))
}

/// `api.v1.edit_file` — direct LayerStack edit publish path.
// PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:366-387 — _edit_file_in_layer_stack
fn op_edit_file(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let layer_path = bound_layer_path(&root, args)?;
    let edits = parse_edits(args)?;
    let stack = LayerStack::open(root.clone())?;
    let (base_bytes, exists) = stack.read_bytes(&layer_path)?;
    let base_hash = hash_current(base_bytes.as_deref(), exists);
    let mut content = if exists {
        String::from_utf8(base_bytes.unwrap_or_default()).map_err(|err| {
            eos_layerstack::LayerStackError::Storage(format!("file is not utf-8 text: {err}"))
        })?
    } else {
        String::new()
    };

    if !exists {
        let manifest = stack.read_active_manifest()?;
        return Ok(guarded_conflict_response(
            "edit",
            &layer_path,
            "aborted_version",
            "aborted_version",
            "file does not exist",
            resource_timings(&manifest, 0),
            total_start,
        ));
    }

    for edit in &edits {
        match apply_search_replace(&content, &edit.old_text, &edit.new_text, edit.replace_all) {
            Ok(next) => content = next,
            Err(err) => {
                let manifest = stack.read_active_manifest()?;
                return Ok(guarded_conflict_response(
                    "edit",
                    &layer_path,
                    "aborted_overlap",
                    "aborted_overlap",
                    search_replace_message(&err),
                    resource_timings(&manifest, 0),
                    total_start,
                ));
            }
        }
    }

    let manifest = stack.read_active_manifest()?;
    drop(stack);
    let occ_start = Instant::now();
    let path = LayerPath::parse(&layer_path).map_err(eos_layerstack::LayerStackError::from)?;
    let result = apply_occ_changeset(
        &root,
        Some(manifest.version as u64),
        &[LayerChange::Write {
            path: path.clone(),
            content: content.into_bytes(),
        }],
        &[(path, base_hash)],
    )?;
    let manifest = LayerStack::open(root)?.read_active_manifest()?;
    let mut timings = resource_timings(&manifest, published_file_count(&result));
    timings.insert(
        "api.edit.occ_apply_s".to_owned(),
        json!(occ_start.elapsed().as_secs_f64()),
    );
    Ok(guarded_changeset_response(
        "edit",
        &result,
        timings,
        total_start,
        Some(edits.len() as i64),
    ))
}

/// `api.v1.shell` — fresh overlay namespace, capture upperdir, publish via OCC.
// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:130-202 — run_tool_call overlay body
fn op_shell(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    run_shell_overlay(args, Instant::now())
}

pub(crate) fn run_shell_overlay(args: &Value, total_start: Instant) -> Result<Value, DaemonError> {
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let command = require_shell_command(args)?;
    let cwd = args
        .get("cwd")
        .and_then(Value::as_str)
        .unwrap_or(".")
        .to_owned();
    let invocation_id = args
        .get("invocation_id")
        .and_then(Value::as_str)
        .unwrap_or("shell")
        .to_owned();
    let agent_id = args
        .get("agent_id")
        .and_then(Value::as_str)
        .unwrap_or("default")
        .to_owned();
    let timeout_seconds = args.get("timeout_seconds").and_then(Value::as_f64);
    let binding = require_workspace_binding(&root)?;

    let mut stack = LayerStack::open(root.clone())?;
    let lease = stack.acquire_snapshot(&format!("overlay:{agent_id}:{invocation_id}"))?;
    let run_result: Result<ShellRunOutcome, DaemonError> = (|| {
        let run_root = overlay_writable_root()
            .map_err(|err| overlay_daemon_error("overlay writable root", err))?
            .join("runtime")
            .join("sandbox-overlay")
            .join(format!(
                "{}-{}",
                std::process::id(),
                sanitize_path_component(&invocation_id)
            ));
        let dirs = allocate_overlay_writable_dirs(&run_root)
            .map_err(|err| overlay_daemon_error("allocate overlay dirs", err))?;
        let _cleanup = RunDirCleanup(dirs.run_dir.clone());
        let request = RunRequest {
            mode: RunMode::FreshNs,
            tool_call: ToolCall {
                invocation_id: invocation_id.clone(),
                agent_id: agent_id.clone(),
                verb: "shell".to_owned(),
                intent: Intent::WriteAllowed,
                args: json!({
                    "command": command,
                    "cwd": cwd,
                }),
                background: args
                    .get("background")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
            },
            workspace_root: WorkspaceRoot(PathBuf::from(&binding.workspace_root)),
            layer_paths: lease.layer_paths.iter().map(PathBuf::from).collect(),
            upperdir: Some(dirs.upperdir.clone()),
            workdir: Some(dirs.workdir.clone()),
            ns_fds: None,
            cgroup_path: None,
            timeout_seconds,
        };
        let runner = run_ns_runner_child(&request)?;
        let capture_start = Instant::now();
        let changes = capture_upperdir(&dirs.upperdir)
            .map_err(|err| overlay_daemon_error("capture upperdir", err))?;
        let capture_s = capture_start.elapsed().as_secs_f64();
        let path_kinds = changes
            .iter()
            .map(|change| {
                (
                    change.path().as_str().to_owned(),
                    layer_change_kind(change).to_owned(),
                )
            })
            .collect();
        let route_start = Instant::now();
        let route_metrics = occ_route_metrics(&root, &changes)?;
        let route_s = route_start.elapsed().as_secs_f64();
        let base_hashes = base_hashes_for_snapshot(&root, &lease.manifest, &changes)?;
        let occ_start = Instant::now();
        let changeset = apply_occ_changeset(
            &root,
            Some(lease.manifest_version as u64),
            &changes,
            &base_hashes,
        )?;
        let occ_s = occ_start.elapsed().as_secs_f64();
        Ok(ShellRunOutcome {
            runner,
            changeset,
            path_kinds,
            route_metrics,
            route_s,
            capture_s,
            occ_s,
        })
    })();
    let _ = stack.release_lease(&lease.lease_id);
    let shell = run_result?;

    let manifest = LayerStack::open(root)?.read_active_manifest()?;
    let mut timings = resource_timings(&manifest, shell.path_kinds.len());
    merge_runner_timings(&mut timings, &shell.runner);
    timings.insert(
        "command_exec.capture_upperdir_s".to_owned(),
        json!(shell.capture_s),
    );
    timings.insert("command_exec.occ_apply_s".to_owned(), json!(shell.occ_s));
    insert_occ_route_timings(
        &mut timings,
        shell.route_metrics,
        shell.route_s,
        shell.occ_s,
    );
    timings.insert(
        "api.shell.total_s".to_owned(),
        json!(total_start.elapsed().as_secs_f64()),
    );
    let mut response =
        guarded_changeset_response("shell", &shell.changeset, timings, total_start, None);
    attach_runner_shell_fields(&mut response, &shell.runner);
    response["changed_path_kinds"] = Value::Object(
        shell
            .path_kinds
            .into_iter()
            .map(|(path, kind)| (path, json!(kind)))
            .collect(),
    );
    if shell.changeset.success() && response["conflict"].is_null() {
        response["success"] = json!(true);
        response["status"] = shell
            .runner
            .tool_result
            .get("status")
            .cloned()
            .unwrap_or_else(|| json!("ok"));
    }
    Ok(response)
}

/// `api.v1.glob` — read-only overlay namespace search.
// PORT backend/src/sandbox/shared/tool_primitives/glob.py:20-35 — glob_files
fn op_glob(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    run_overlay_read_tool(args, "glob")
}

/// `api.v1.grep` — read-only overlay namespace content search.
// PORT backend/src/sandbox/shared/tool_primitives/grep.py:36-102 — grep_files
fn op_grep(args: &Value, _context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    run_overlay_read_tool(args, "grep")
}

fn run_overlay_read_tool(args: &Value, verb: &str) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let invocation_id = args
        .get("invocation_id")
        .and_then(Value::as_str)
        .unwrap_or(verb)
        .to_owned();
    let agent_id = args
        .get("agent_id")
        .and_then(Value::as_str)
        .unwrap_or("default")
        .to_owned();
    let binding = require_workspace_binding(&root)?;

    let mut stack = LayerStack::open(root.clone())?;
    let lease = stack.acquire_snapshot(&format!("overlay:{agent_id}:{invocation_id}"))?;
    let run_result: Result<RunResult, DaemonError> = (|| {
        let run_root = overlay_writable_root()
            .map_err(|err| overlay_daemon_error("overlay writable root", err))?
            .join("runtime")
            .join("sandbox-overlay")
            .join(format!(
                "{}-{}",
                std::process::id(),
                sanitize_path_component(&invocation_id)
            ));
        let dirs = allocate_overlay_writable_dirs(&run_root)
            .map_err(|err| overlay_daemon_error("allocate overlay dirs", err))?;
        let _cleanup = RunDirCleanup(dirs.run_dir.clone());
        let request = RunRequest {
            mode: RunMode::FreshNs,
            tool_call: ToolCall {
                invocation_id: invocation_id.clone(),
                agent_id,
                verb: verb.to_owned(),
                intent: Intent::ReadOnly,
                args: args.clone(),
                background: false,
            },
            workspace_root: WorkspaceRoot(PathBuf::from(&binding.workspace_root)),
            layer_paths: lease.layer_paths.iter().map(PathBuf::from).collect(),
            upperdir: Some(dirs.upperdir),
            workdir: Some(dirs.workdir),
            ns_fds: None,
            cgroup_path: None,
            timeout_seconds: args.get("timeout_seconds").and_then(Value::as_f64),
        };
        run_ns_runner_child(&request)
    })();
    let _ = stack.release_lease(&lease.lease_id);

    let runner = run_result?;
    let manifest = LayerStack::open(root)?.read_active_manifest()?;
    let mut timings = resource_timings(&manifest, 0);
    merge_runner_timings(&mut timings, &runner);
    let mut response = runner.tool_result;
    timings
        .entry("command_exec.capture_upperdir_s".to_owned())
        .or_insert_with(|| json!(0.0));
    timings.insert(
        "command_exec.total_s".to_owned(),
        json!(total_start.elapsed().as_secs_f64()),
    );
    timings.insert(
        format!("api.{verb}.total_s"),
        json!(total_start.elapsed().as_secs_f64()),
    );
    response["timings"] = Value::Object(timings);
    Ok(response)
}

fn require_string(args: &Value, key: &str) -> Result<String, DaemonError> {
    let value = args
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_owned();
    if value.is_empty() {
        return Err(DaemonError::InvalidEnvelope(format!("{key} is required")));
    }
    Ok(value)
}

fn binding_to_value(binding: &WorkspaceBinding) -> Result<Value, DaemonError> {
    serde_json::to_value(binding).map_err(|err| DaemonError::InvalidEnvelope(err.to_string()))
}

fn timings_to_value_map(
    timings: &std::collections::BTreeMap<String, f64>,
) -> serde_json::Map<String, Value> {
    timings
        .iter()
        .map(|(key, value)| (key.clone(), json!(value)))
        .collect()
}

fn require_shell_command(args: &Value) -> Result<Value, DaemonError> {
    let Some(command) = args.get("command") else {
        return Err(DaemonError::InvalidEnvelope(
            "command is required".to_owned(),
        ));
    };
    if let Some(value) = command.as_str() {
        if value.trim().is_empty() {
            return Err(DaemonError::InvalidEnvelope(
                "command must be a non-empty string".to_owned(),
            ));
        }
        return Ok(json!(value));
    }
    if let Some(parts) = command.as_array() {
        if parts.is_empty() {
            return Err(DaemonError::InvalidEnvelope(
                "command argv must not be empty".to_owned(),
            ));
        }
        for (index, part) in parts.iter().enumerate() {
            let Some(value) = part.as_str() else {
                return Err(DaemonError::InvalidEnvelope(
                    "command argv entries must be strings".to_owned(),
                ));
            };
            if index == 0 && value.trim().is_empty() {
                return Err(DaemonError::InvalidEnvelope(
                    "command argv[0] must not be empty".to_owned(),
                ));
            }
        }
        return Ok(Value::Array(parts.clone()));
    }
    Err(DaemonError::InvalidEnvelope(
        "command must be a string or argv list".to_owned(),
    ))
}

#[derive(Clone)]
struct LayerStackCommitTransaction {
    root: PathBuf,
}

struct ShellRunOutcome {
    runner: RunResult,
    changeset: ChangesetResult,
    path_kinds: Vec<(String, String)>,
    route_metrics: OccRouteMetrics,
    route_s: f64,
    capture_s: f64,
    occ_s: f64,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct OccRouteMetrics {
    gated_path_count: usize,
    direct_path_count: usize,
}

struct RunDirCleanup(PathBuf);

impl Drop for RunDirCleanup {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

impl CommitTransactionPort for LayerStackCommitTransaction {
    fn revalidate_and_publish(
        &self,
        combined: &PreparedChangeset,
    ) -> std::result::Result<ChangesetResult, PublishConflict> {
        let total_start = Instant::now();
        let mut stack = match LayerStack::open(self.root.clone()) {
            Ok(stack) => stack,
            Err(err) => {
                let timings =
                    commit_timings(combined, 0.0, 0.0, total_start.elapsed().as_secs_f64());
                return Ok(failed_changeset_with_timings(
                    combined,
                    err.to_string(),
                    timings,
                ));
            }
        };
        let validate_start = Instant::now();
        let active = match stack.read_active_manifest() {
            Ok(manifest) => manifest,
            Err(err) => {
                let timings =
                    commit_timings(combined, 0.0, 0.0, total_start.elapsed().as_secs_f64());
                return Ok(failed_changeset_with_timings(
                    combined,
                    err.to_string(),
                    timings,
                ));
            }
        };
        let view = MergedView::new(self.root.clone());
        let validations = validate_prepared(&self.root, &view, &active, combined);
        let validate_s = validate_start.elapsed().as_secs_f64();
        if combined.atomic
            && validations
                .iter()
                .any(|file| is_validation_failure(file.status))
        {
            return Ok(ChangesetResult {
                files: validations
                    .into_iter()
                    .map(|file| {
                        if file.status.is_published() {
                            FileResult {
                                status: OccStatus::Dropped,
                                message: "not published because atomic changeset validation failed"
                                    .to_owned(),
                                ..file
                            }
                        } else {
                            file
                        }
                    })
                    .collect(),
                published_manifest_version: None,
                timings: commit_timings(
                    combined,
                    validate_s,
                    0.0,
                    total_start.elapsed().as_secs_f64(),
                ),
            });
        }
        let publishable_paths = validations
            .iter()
            .filter(|file| file.status.is_published())
            .map(|file| file.path.as_str())
            .collect::<HashSet<_>>();
        let publishable_changes: Vec<LayerChange> = combined
            .changes
            .iter()
            .filter(|change| publishable_paths.contains(change.path().as_str()))
            .cloned()
            .collect();
        if publishable_changes.is_empty() {
            return Ok(ChangesetResult {
                files: validations,
                published_manifest_version: None,
                timings: commit_timings(
                    combined,
                    validate_s,
                    0.0,
                    total_start.elapsed().as_secs_f64(),
                ),
            });
        }
        let publish_start = Instant::now();
        match stack.publish_layer(&publishable_changes) {
            Ok(manifest) => {
                let publish_s = publish_start.elapsed().as_secs_f64();
                let maintenance_start = Instant::now();
                let mut squash_applied = 0.0;
                if stack.can_squash(AUTO_SQUASH_MAX_DEPTH).unwrap_or(false)
                    && stack
                        .squash(AUTO_SQUASH_MAX_DEPTH)
                        .map(|squashed| squashed.is_some())
                        .unwrap_or(false)
                {
                    squash_applied = 1.0;
                }
                let maintenance_s = maintenance_start.elapsed().as_secs_f64();
                let mut timings = commit_timings(
                    combined,
                    validate_s,
                    publish_s,
                    total_start.elapsed().as_secs_f64(),
                );
                timings.insert("occ.maintenance.total_s".to_owned(), maintenance_s);
                timings.insert("occ.maintenance.squash_applied".to_owned(), squash_applied);
                Ok(ChangesetResult {
                    files: validations
                        .into_iter()
                        .map(|file| {
                            if file.status.is_published() {
                                FileResult {
                                    status: OccStatus::Committed,
                                    ..file
                                }
                            } else {
                                file
                            }
                        })
                        .collect(),
                    published_manifest_version: Some(manifest.version as u64),
                    timings,
                })
            }
            Err(eos_layerstack::LayerStackError::ManifestConflict { found, .. }) => {
                Err(PublishConflict {
                    observed_version: Some(found as u64),
                })
            }
            Err(err) => {
                let publish_s = publish_start.elapsed().as_secs_f64();
                Ok(failed_changeset_with_timings(
                    combined,
                    err.to_string(),
                    commit_timings(
                        combined,
                        validate_s,
                        publish_s,
                        total_start.elapsed().as_secs_f64(),
                    ),
                ))
            }
        }
    }
}

#[derive(Clone)]
struct LayerStackRouteProvider {
    root: PathBuf,
}

impl OccRouteProvider for LayerStackRouteProvider {
    fn is_ignored(&self, path: &LayerPath) -> std::result::Result<bool, eos_occ::OccError> {
        let stack = LayerStack::open(self.root.clone())
            .map_err(|err| eos_occ::OccError::RoutePreparation(err.to_string()))?;
        let (bytes, exists) = stack
            .read_bytes(".gitignore")
            .map_err(|err| eos_occ::OccError::RoutePreparation(err.to_string()))?;
        if !exists {
            return Ok(false);
        }
        let Some(bytes) = bytes else {
            return Ok(false);
        };
        let ignore = String::from_utf8(bytes)
            .map_err(|err| eos_occ::OccError::RoutePreparation(err.to_string()))?;
        Ok(gitignore_matches(&ignore, path.as_str()))
    }

    fn base_hash(
        &self,
        path: &LayerPath,
    ) -> std::result::Result<Option<String>, eos_occ::OccError> {
        let stack = LayerStack::open(self.root.clone())
            .map_err(|err| eos_occ::OccError::RoutePreparation(err.to_string()))?;
        let (bytes, exists) = stack
            .read_bytes(path.as_str())
            .map_err(|err| eos_occ::OccError::RoutePreparation(err.to_string()))?;
        Ok(hash_current(bytes.as_deref(), exists))
    }
}

pub(crate) fn apply_occ_changeset(
    root: &Path,
    snapshot_version: Option<u64>,
    changes: &[LayerChange],
    base_hashes: &[(LayerPath, Option<String>)],
) -> Result<ChangesetResult, DaemonError> {
    let lookup = occ_service_for_root(root)?;
    let mut result = lookup.service.apply_changeset_with_base_hashes(
        changes,
        snapshot_version,
        true,
        base_hashes,
    )?;
    lookup.insert_timings(&mut result.timings);
    Ok(result)
}

pub(crate) fn occ_route_metrics(
    root: &Path,
    changes: &[LayerChange],
) -> Result<OccRouteMetrics, DaemonError> {
    let stack = LayerStack::open(root.to_path_buf())?;
    let ignore = match stack.read_bytes(".gitignore")? {
        (Some(bytes), true) => {
            String::from_utf8(bytes).map_err(|err| DaemonError::InvalidEnvelope(err.to_string()))?
        }
        _ => String::new(),
    };
    let mut metrics = OccRouteMetrics::default();
    for change in changes {
        let path = change.path().as_str();
        if path == ".git" || path.starts_with(".git/") {
            continue;
        }
        if gitignore_matches(&ignore, path) {
            metrics.direct_path_count += 1;
        } else {
            metrics.gated_path_count += 1;
        }
    }
    Ok(metrics)
}

pub(crate) fn insert_occ_route_timings(
    timings: &mut serde_json::Map<String, Value>,
    metrics: OccRouteMetrics,
    route_s: f64,
    occ_s: f64,
) {
    for (key, value) in [
        ("occ.prepare.prepare_groups_s", route_s),
        ("occ.prepare.group_by_route_s", route_s),
        ("occ.prepare.route_and_base_hash_s", route_s),
        ("occ.prepare.total_s", route_s),
        ("occ.commit.total_s", occ_s),
        (
            "occ.commit.gated_path_count",
            metrics.gated_path_count as f64,
        ),
        (
            "occ.commit.direct_path_count",
            metrics.direct_path_count as f64,
        ),
    ] {
        timings.insert(key.to_owned(), json!(value));
    }
    for key in [
        "occ.commit.validate_groups_s",
        "occ.commit.publish_layer_s",
        "occ.commit.stager_write_total_s",
        "occ.commit.stager_write_count",
        "occ.commit.gated_read_current_total_s",
        "occ.commit.gated_apply_changes_total_s",
        "occ.commit.gated_stage_delta_total_s",
        "occ.commit.direct_read_current_total_s",
        "occ.commit.direct_apply_changes_total_s",
        "occ.commit.direct_stage_delta_total_s",
    ] {
        timings.entry(key.to_owned()).or_insert_with(|| json!(0.0));
    }
}

pub(crate) fn run_ns_runner_child(request: &RunRequest) -> Result<RunResult, DaemonError> {
    let payload =
        serde_json::to_vec(request).map_err(|err| DaemonError::InvalidEnvelope(err.to_string()))?;
    let mut child = Command::new(std::env::current_exe()?)
        .arg("ns-runner")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    child
        .stdin
        .as_mut()
        .ok_or_else(|| {
            DaemonError::Ephemeral(eos_ephemeral::EphemeralError::Overlay(
                "ns-runner stdin unavailable".to_owned(),
            ))
        })?
        .write_all(&payload)?;
    let output = child.wait_with_output()?;
    if !output.status.success() {
        return Err(DaemonError::Ephemeral(
            eos_ephemeral::EphemeralError::Overlay(format!(
                "ns-runner exited with status {}: {}",
                output.status,
                String::from_utf8_lossy(&output.stderr)
            )),
        ));
    }
    serde_json::from_slice::<RunResult>(&output.stdout).map_err(|err| {
        DaemonError::Ephemeral(eos_ephemeral::EphemeralError::Overlay(format!(
            "invalid ns-runner output: {err}"
        )))
    })
}

pub(crate) fn base_hashes_for_snapshot(
    root: &Path,
    manifest: &eos_layerstack::Manifest,
    changes: &[LayerChange],
) -> Result<Vec<(LayerPath, Option<String>)>, DaemonError> {
    let view = MergedView::new(root.to_path_buf());
    changes
        .iter()
        .map(|change| {
            let (bytes, exists) = view.read_bytes(change.path().as_str(), manifest)?;
            Ok((
                change.path().clone(),
                hash_current(bytes.as_deref(), exists),
            ))
        })
        .collect()
}

pub(crate) fn attach_runner_shell_fields(response: &mut Value, runner: &RunResult) {
    response["exit_code"] = runner
        .tool_result
        .get("exit_code")
        .cloned()
        .unwrap_or_else(|| json!(runner.exit_code));
    response["stdout"] = runner
        .tool_result
        .get("stdout")
        .cloned()
        .unwrap_or_else(|| json!(""));
    response["stderr"] = runner
        .tool_result
        .get("stderr")
        .cloned()
        .unwrap_or_else(|| json!(""));
    response["warnings"] = runner
        .tool_result
        .get("warnings")
        .cloned()
        .unwrap_or_else(|| json!([]));
}

pub(crate) fn merge_runner_timings(
    timings: &mut serde_json::Map<String, Value>,
    runner: &RunResult,
) {
    if let Some(runner_timings) = runner.tool_result.get("timings").and_then(Value::as_object) {
        for (key, value) in runner_timings {
            timings.entry(key.clone()).or_insert_with(|| value.clone());
        }
    }
    if let Some(value) = timings.get("workspace.mount_s").cloned() {
        timings
            .entry("command_exec.mount_workspace_s".to_owned())
            .or_insert(value);
    }
    if let Some(value) = timings.get("workspace.tool_s").cloned() {
        timings
            .entry("command_exec.run_command_s".to_owned())
            .or_insert(value);
    }
}

pub(crate) fn layer_change_kind(change: &LayerChange) -> &'static str {
    match change {
        LayerChange::Write { .. } => "write",
        LayerChange::Delete { .. } => "delete",
        LayerChange::Symlink { .. } => "symlink",
        LayerChange::OpaqueDir { .. } => "opaque_dir",
    }
}

pub(crate) fn overlay_daemon_error(context: &str, err: eos_overlay::OverlayError) -> DaemonError {
    DaemonError::Ephemeral(eos_ephemeral::EphemeralError::Overlay(format!(
        "{context}: {err}"
    )))
}

fn sanitize_path_component(value: &str) -> String {
    let cleaned: String = value
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.') {
                ch
            } else {
                '_'
            }
        })
        .collect();
    if cleaned.is_empty() {
        "op".to_owned()
    } else {
        cleaned
    }
}

const OCC_SERVICE_CACHE_MAX: usize = 256;

struct OccServiceLookup {
    service: Arc<OccService<LayerStackCommitTransaction>>,
    lock_wait_s: f64,
    cache_hit: bool,
    cache_created: bool,
    evicted_count: usize,
    cache_size: usize,
}

impl OccServiceLookup {
    fn insert_timings(&self, timings: &mut BTreeMap<String, f64>) {
        for (key, value) in [
            ("occ.runtime_service.cache_lock_wait_s", self.lock_wait_s),
            (
                "occ.runtime_service.cache_hit",
                if self.cache_hit { 1.0 } else { 0.0 },
            ),
            (
                "occ.runtime_service.cache_miss",
                if self.cache_hit { 0.0 } else { 1.0 },
            ),
            (
                "occ.runtime_service.cache_created",
                if self.cache_created { 1.0 } else { 0.0 },
            ),
            (
                "occ.runtime_service.cache_reused",
                if self.cache_created { 0.0 } else { 1.0 },
            ),
            (
                "occ.runtime_service.cache_evicted_count",
                self.evicted_count as f64,
            ),
            ("occ.runtime_service.cache_size", self.cache_size as f64),
            (
                "occ.runtime_service.cache_capacity",
                OCC_SERVICE_CACHE_MAX as f64,
            ),
        ] {
            timings.entry(key.to_owned()).or_insert(value);
        }
    }
}

#[derive(Default)]
struct OccServiceCacheStats {
    hits_total: u64,
    misses_total: u64,
    creates_total: u64,
    evictions_total: u64,
    lock_wait_s_total: f64,
    lock_wait_s_max: f64,
}

#[derive(Default)]
struct OccServiceCache {
    entries: HashMap<String, Arc<OccService<LayerStackCommitTransaction>>>,
    lru: VecDeque<String>,
    stats: OccServiceCacheStats,
}

impl OccServiceCache {
    fn record_lock_wait(&mut self, lock_wait_s: f64) {
        self.stats.lock_wait_s_total += lock_wait_s;
        self.stats.lock_wait_s_max = self.stats.lock_wait_s_max.max(lock_wait_s);
    }

    fn get(&mut self, key: &str, lock_wait_s: f64) -> Option<OccServiceLookup> {
        self.record_lock_wait(lock_wait_s);
        let service = self.entries.get(key)?.clone();
        self.touch(key);
        self.stats.hits_total += 1;
        Some(OccServiceLookup {
            service,
            lock_wait_s,
            cache_hit: true,
            cache_created: false,
            evicted_count: 0,
            cache_size: self.entries.len(),
        })
    }

    fn insert_or_get(
        &mut self,
        key: String,
        service: Arc<OccService<LayerStackCommitTransaction>>,
        lock_wait_s: f64,
    ) -> OccServiceLookup {
        self.record_lock_wait(lock_wait_s);
        if let Some(existing) = self.entries.get(&key).cloned() {
            self.touch(&key);
            self.stats.hits_total += 1;
            return OccServiceLookup {
                service: existing,
                lock_wait_s,
                cache_hit: true,
                cache_created: false,
                evicted_count: 0,
                cache_size: self.entries.len(),
            };
        }
        self.stats.misses_total += 1;
        self.stats.creates_total += 1;
        self.lru.push_back(key.clone());
        self.entries.insert(key, service.clone());
        let evicted_count = self.evict_oldest();
        self.stats.evictions_total += evicted_count as u64;
        OccServiceLookup {
            service,
            lock_wait_s,
            cache_hit: false,
            cache_created: true,
            evicted_count,
            cache_size: self.entries.len(),
        }
    }

    fn touch(&mut self, key: &str) {
        if let Some(position) = self.lru.iter().position(|entry| entry == key) {
            self.lru.remove(position);
        }
        self.lru.push_back(key.to_owned());
    }

    fn evict_oldest(&mut self) -> usize {
        let mut evicted_count = 0;
        while self.entries.len() > OCC_SERVICE_CACHE_MAX {
            let Some(key) = self.lru.pop_front() else {
                break;
            };
            if self.entries.remove(&key).is_some() {
                evicted_count += 1;
            }
        }
        evicted_count
    }
}

fn occ_service_for_root(root: &Path) -> Result<OccServiceLookup, DaemonError> {
    let key = normalize_root_key(root);
    let lock_start = Instant::now();
    {
        let mut cache = lock_occ_services()?;
        if let Some(lookup) = cache.get(&key, lock_start.elapsed().as_secs_f64()) {
            return Ok(lookup);
        }
    }
    let transaction = LayerStackCommitTransaction {
        root: root.to_path_buf(),
    };
    let route_provider = Arc::new(LayerStackRouteProvider {
        root: root.to_path_buf(),
    });
    let service = Arc::new(OccService::with_route_provider(
        CommitQueue::new(transaction),
        route_provider,
    )?);
    let lock_start = Instant::now();
    let mut cache = lock_occ_services()?;
    Ok(cache.insert_or_get(key, service.clone(), lock_start.elapsed().as_secs_f64()))
}

fn occ_services() -> &'static Mutex<OccServiceCache> {
    static SERVICES: OnceLock<Mutex<OccServiceCache>> = OnceLock::new();
    SERVICES.get_or_init(|| Mutex::new(OccServiceCache::default()))
}

fn lock_occ_services() -> Result<MutexGuard<'static, OccServiceCache>, DaemonError> {
    occ_services()
        .lock()
        .map_err(|_| DaemonError::StateLockPoisoned("occ service registry"))
}

fn normalize_root_key(root: &Path) -> String {
    root.canonicalize()
        .unwrap_or_else(|_| root.to_path_buf())
        .to_string_lossy()
        .into_owned()
}

fn occ_service_cache_snapshot() -> Value {
    let lock_start = Instant::now();
    let mut cache = match lock_occ_services() {
        Ok(cache) => cache,
        Err(err) => {
            return json!({
                "capacity": OCC_SERVICE_CACHE_MAX,
                "size": 0,
                "poisoned": true,
                "error": err.to_string(),
            });
        }
    };
    let lock_wait_s = lock_start.elapsed().as_secs_f64();
    cache.record_lock_wait(lock_wait_s);
    json!({
        "capacity": OCC_SERVICE_CACHE_MAX,
        "size": cache.entries.len(),
        "hits_total": cache.stats.hits_total,
        "misses_total": cache.stats.misses_total,
        "creates_total": cache.stats.creates_total,
        "evictions_total": cache.stats.evictions_total,
        "lock_wait_s_total": cache.stats.lock_wait_s_total,
        "lock_wait_s_max": cache.stats.lock_wait_s_max,
        "last_lock_wait_s": lock_wait_s,
    })
}

#[cfg(test)]
fn clear_occ_service_cache_for_tests() {
    let mut cache = occ_services()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    *cache = OccServiceCache::default();
}

fn validate_prepared(
    root: &Path,
    view: &MergedView,
    manifest: &Manifest,
    prepared: &PreparedChangeset,
) -> Vec<FileResult> {
    let mut parent_absent_cache = HashMap::new();
    prepared
        .path_groups
        .iter()
        .map(|group| match group.route {
            Route::Drop => FileResult {
                path: group.path.clone(),
                status: OccStatus::Dropped,
                message: group
                    .message
                    .clone()
                    .unwrap_or_else(|| "change dropped".to_owned()),
            },
            Route::Reject => FileResult {
                path: group.path.clone(),
                status: OccStatus::Rejected,
                message: group
                    .message
                    .clone()
                    .unwrap_or_else(|| "change rejected".to_owned()),
            },
            Route::Direct => validate_direct_group(&group.path),
            Route::Gated => validate_gated_group(
                root,
                view,
                manifest,
                prepared,
                &group.path,
                &group.base_hash,
                &mut parent_absent_cache,
            ),
            _ => FileResult {
                path: group.path.clone(),
                status: OccStatus::Rejected,
                message: "unsupported route".to_owned(),
            },
        })
        .collect()
}

fn validate_direct_group(path: &LayerPath) -> FileResult {
    FileResult {
        path: path.clone(),
        status: OccStatus::Accepted,
        message: String::new(),
    }
}

fn validate_gated_group(
    root: &Path,
    view: &MergedView,
    manifest: &Manifest,
    prepared: &PreparedChangeset,
    path: &LayerPath,
    base_hash: &Option<String>,
    parent_absent_cache: &mut HashMap<String, bool>,
) -> FileResult {
    let path_str = path.as_str();
    if prepared.changes.iter().any(|change| {
        change.path().as_str() == path_str && matches!(change, LayerChange::Symlink { .. })
    }) {
        return FileResult {
            path: path.clone(),
            status: OccStatus::Rejected,
            message: "unsupported gated change kind: SymlinkChange".to_owned(),
        };
    }
    if base_hash.is_none() {
        if let Some(parent) = parent_dir(path_str) {
            let parent_absent = *parent_absent_cache
                .entry(parent.to_owned())
                .or_insert_with(|| parent_absent_from_manifest(root, manifest, parent));
            if parent_absent {
                return FileResult {
                    path: path.clone(),
                    status: OccStatus::Accepted,
                    message: String::new(),
                };
            }
        }
    }
    match view.read_bytes(path_str, manifest) {
        Ok((bytes, exists)) if hash_current(bytes.as_deref(), exists) == *base_hash => FileResult {
            path: path.clone(),
            status: OccStatus::Accepted,
            message: String::new(),
        },
        Ok(_) => FileResult {
            path: path.clone(),
            status: OccStatus::AbortedVersion,
            message: "content changed".to_owned(),
        },
        Err(err) => FileResult {
            path: path.clone(),
            status: OccStatus::Failed,
            message: err.to_string(),
        },
    }
}

fn parent_dir(path: &str) -> Option<&str> {
    path.rsplit_once('/')
        .map(|(parent, _)| parent)
        .filter(|parent| !parent.is_empty())
}

fn parent_absent_from_manifest(root: &Path, manifest: &Manifest, parent: &str) -> bool {
    manifest.layers.iter().all(|layer| {
        let path = PathBuf::from(&layer.path);
        let layer_dir = if path.is_absolute() {
            path
        } else {
            root.join(path)
        };
        match std::fs::symlink_metadata(layer_dir.join(parent)) {
            Ok(_) => false,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => true,
            Err(_) => false,
        }
    })
}

fn is_validation_failure(status: OccStatus) -> bool {
    matches!(
        status,
        OccStatus::AbortedOverlap
            | OccStatus::AbortedVersion
            | OccStatus::Failed
            | OccStatus::Rejected
    )
}

fn hash_current(content: Option<&[u8]>, exists: bool) -> Option<String> {
    if !exists {
        return None;
    }
    content.map(hash_bytes)
}

fn hash_bytes(content: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(content);
    hex_lower(&hasher.finalize())
}

fn hex_lower(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

fn gitignore_matches(ignore: &str, path: &str) -> bool {
    let mut matched = false;
    for line in ignore.lines() {
        let mut pattern = line.trim();
        if pattern.is_empty() || pattern.starts_with('#') {
            continue;
        }
        let negated = pattern.starts_with('!');
        if negated {
            pattern = pattern.trim_start_matches('!');
        }
        if pattern.is_empty() {
            continue;
        }
        if gitignore_rule_matches(pattern, path) {
            matched = !negated;
        }
    }
    matched
}

fn gitignore_rule_matches(pattern: &str, path: &str) -> bool {
    let pattern = pattern.trim_start_matches('/');
    let dir_only = pattern.ends_with('/');
    let pattern = pattern.trim_end_matches('/');
    if pattern.is_empty() {
        return false;
    }
    if dir_only {
        return path == pattern || path.starts_with(&format!("{pattern}/"));
    }
    if pattern.contains('*') {
        return wildcard_match(pattern.as_bytes(), path.as_bytes());
    }
    if pattern.contains('/') {
        return path == pattern;
    }
    path.split('/').any(|part| part == pattern)
}

fn wildcard_match(pattern: &[u8], value: &[u8]) -> bool {
    let (mut p, mut v) = (0, 0);
    let mut star = None;
    let mut star_value = 0;
    while v < value.len() {
        if p < pattern.len() && (pattern[p] == b'?' || pattern[p] == value[v]) {
            p += 1;
            v += 1;
        } else if p < pattern.len() && pattern[p] == b'*' {
            star = Some(p);
            p += 1;
            star_value = v;
        } else if let Some(star_index) = star {
            p = star_index + 1;
            star_value += 1;
            v = star_value;
        } else {
            return false;
        }
    }
    while p < pattern.len() && pattern[p] == b'*' {
        p += 1;
    }
    p == pattern.len()
}

fn failed_changeset_with_timings(
    prepared: &PreparedChangeset,
    message: String,
    timings: BTreeMap<String, f64>,
) -> ChangesetResult {
    ChangesetResult {
        files: prepared
            .path_groups
            .iter()
            .map(|group| FileResult {
                path: group.path.clone(),
                status: OccStatus::Failed,
                message: message.clone(),
            })
            .collect(),
        published_manifest_version: None,
        timings,
    }
}

fn commit_timings(
    prepared: &PreparedChangeset,
    validate_s: f64,
    publish_s: f64,
    total_s: f64,
) -> BTreeMap<String, f64> {
    let mut timings = BTreeMap::new();
    timings.insert("occ.commit.total_s".to_owned(), total_s);
    timings.insert("occ.commit.validate_groups_s".to_owned(), validate_s);
    timings.insert("occ.commit.publish_layer_s".to_owned(), publish_s);
    timings.insert(
        "occ.commit.stager_write_count".to_owned(),
        prepared.changes.len() as f64,
    );
    timings.insert("occ.commit.stager_write_total_s".to_owned(), publish_s);
    timings.insert(
        "occ.commit.gated_path_count".to_owned(),
        prepared
            .path_groups
            .iter()
            .filter(|group| group.route == Route::Gated)
            .count() as f64,
    );
    timings.insert(
        "occ.commit.direct_path_count".to_owned(),
        prepared
            .path_groups
            .iter()
            .filter(|group| group.route == Route::Direct)
            .count() as f64,
    );
    for key in [
        "occ.commit.gated_read_current_total_s",
        "occ.commit.gated_apply_changes_total_s",
        "occ.commit.gated_stage_delta_total_s",
        "occ.commit.direct_read_current_total_s",
        "occ.commit.direct_apply_changes_total_s",
        "occ.commit.direct_stage_delta_total_s",
    ] {
        timings.insert(key.to_owned(), 0.0);
    }
    timings
}

fn bound_layer_path(root: &Path, args: &Value) -> Result<String, DaemonError> {
    let raw_path = require_string(args, "path")?;
    let binding = require_workspace_binding(root)?;
    if raw_path.starts_with('/') {
        binding
            .layer_path_from_absolute(&raw_path)
            .map_err(DaemonError::from)
    } else {
        binding
            .layer_path_from_relative(&raw_path)
            .map_err(DaemonError::from)
    }
}

fn parse_edits(args: &Value) -> Result<Vec<SearchReplaceEdit>, DaemonError> {
    let edits = args
        .get("edits")
        .and_then(Value::as_array)
        .ok_or_else(|| DaemonError::InvalidEnvelope("edits must be a list".to_owned()))?;
    let mut parsed = Vec::with_capacity(edits.len());
    for raw in edits {
        let edit: SearchReplaceEdit = serde_json::from_value(raw.clone())
            .map_err(|err| DaemonError::InvalidEnvelope(err.to_string()))?;
        if edit.old_text.is_empty() {
            return Err(DaemonError::InvalidEnvelope(
                "edit anchor old_text must be non-empty".to_owned(),
            ));
        }
        parsed.push(edit);
    }
    Ok(parsed)
}

pub(crate) fn guarded_changeset_response(
    verb: &str,
    result: &ChangesetResult,
    mut timings: serde_json::Map<String, Value>,
    total_start: Instant,
    applied_edits: Option<i64>,
) -> Value {
    for (key, value) in &result.timings {
        timings.insert(key.clone(), json!(value));
    }
    timings.insert(
        format!("api.{verb}.total_s"),
        json!(total_start.elapsed().as_secs_f64()),
    );
    let changed_paths: Vec<String> = result
        .files
        .iter()
        .filter(|file| file.status.is_published())
        .map(|file| file.path.as_str().to_owned())
        .collect();
    let mut changed_path_kinds = serde_json::Map::new();
    for path in &changed_paths {
        changed_path_kinds.insert(path.to_owned(), json!("write"));
    }
    let conflict = first_conflict(result);
    let mut response = json!({
        "success": result.success(),
        "workspace": "ephemeral",
        "changed_paths": changed_paths,
        "changed_path_kinds": Value::Object(changed_path_kinds),
        "mutation_source": mutation_source(verb),
        "status": conflict.as_ref().map(|file| occ_status_wire(file.status)).unwrap_or("committed"),
        "conflict": conflict.as_ref().map(|file| json!({
            "reason": occ_status_wire(file.status),
            "conflict_file": file.path.as_str(),
            "message": if file.message.is_empty() { occ_status_wire(file.status) } else { file.message.as_str() },
        })),
        "conflict_reason": conflict.as_ref().map(|file| {
            if file.message.is_empty() { occ_status_wire(file.status) } else { file.message.as_str() }
        }),
        "error": null,
        "timings": Value::Object(timings),
    });
    if let Some(count) = applied_edits {
        response["applied_edits"] = json!(count);
    }
    response
}

fn first_conflict(result: &ChangesetResult) -> Option<&FileResult> {
    result.files.iter().find(|file| !file.status.is_success())
}

fn published_file_count(result: &ChangesetResult) -> usize {
    result
        .files
        .iter()
        .filter(|file| file.status.is_published())
        .count()
}

fn occ_status_wire(status: OccStatus) -> &'static str {
    match status {
        OccStatus::Accepted => "accepted",
        OccStatus::Committed => "committed",
        OccStatus::AbortedVersion => "aborted_version",
        OccStatus::AbortedOverlap => "aborted_overlap",
        OccStatus::Dropped => "dropped",
        OccStatus::Rejected => "rejected",
        OccStatus::Failed => "failed",
        _ => "failed",
    }
}

fn guarded_conflict_response(
    verb: &str,
    path: &str,
    status: &str,
    reason: &str,
    message: &str,
    mut timings: serde_json::Map<String, Value>,
    total_start: Instant,
) -> Value {
    timings.insert(
        format!("api.{verb}.total_s"),
        json!(total_start.elapsed().as_secs_f64()),
    );
    let mut response = json!({
        "success": false,
        "workspace": "ephemeral",
        "changed_paths": [],
        "changed_path_kinds": {},
        "mutation_source": mutation_source(verb),
        "status": status,
        "conflict": {
            "reason": reason,
            "conflict_file": path,
            "message": message,
        },
        "conflict_reason": reason,
        "error": null,
        "timings": Value::Object(timings),
    });
    if verb == "edit" {
        response["applied_edits"] = json!(0);
    }
    response
}

pub(crate) fn resource_timings(
    manifest: &eos_layerstack::Manifest,
    changed_path_count: usize,
) -> serde_json::Map<String, Value> {
    let mut timings = serde_json::Map::new();
    timings.insert(
        "resource.command_exec.changed_path_count".to_owned(),
        json!(changed_path_count as f64),
    );
    timings.insert(
        "resource.layer_stack.manifest_depth".to_owned(),
        json!(manifest.depth() as f64),
    );
    timings.insert(
        "resource.layer_stack.manifest_path_count".to_owned(),
        json!(manifest.depth() as f64),
    );
    for key in [
        "resource.command_exec.run_dir_tree_exists",
        "resource.command_exec.run_dir_tree_bytes",
        "resource.command_exec.run_dir_tree_file_count",
        "resource.command_exec.run_dir_tree_dir_count",
        "resource.command_exec.run_dir_tree_entry_count",
        "resource.command_exec.run_dir_tree_truncated",
        "resource.command_exec.workspace_tree_exists",
        "resource.command_exec.workspace_tree_bytes",
        "resource.command_exec.workspace_tree_file_count",
        "resource.command_exec.workspace_tree_dir_count",
        "resource.command_exec.workspace_tree_entry_count",
        "resource.command_exec.workspace_tree_truncated",
        "resource.command_exec.upperdir_tree_exists",
        "resource.command_exec.upperdir_tree_bytes",
        "resource.command_exec.upperdir_tree_file_count",
        "resource.command_exec.upperdir_tree_dir_count",
        "resource.command_exec.upperdir_tree_entry_count",
        "resource.command_exec.upperdir_tree_truncated",
    ] {
        timings.insert(key.to_owned(), json!(0.0));
    }
    timings
}

fn mutation_source(verb: &str) -> &'static str {
    match verb {
        "write" => "api_write",
        "edit" => "api_edit",
        "shell" => "overlay_capture",
        _ => "",
    }
}

fn search_replace_message(err: &SearchReplaceError) -> &'static str {
    match err {
        SearchReplaceError::EmptyAnchor => "edit anchor old_text must be non-empty",
        SearchReplaceError::NotFound => "anchor not found",
        SearchReplaceError::CountMismatch => "anchor occurrence count mismatch",
        _ => "edit failed",
    }
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

fn attach_runtime_timings(response: &mut Value) {
    let Some(obj) = response.as_object_mut() else {
        return;
    };
    let timings = obj
        .entry("timings")
        .or_insert_with(|| Value::Object(serde_json::Map::new()));
    if let Value::Object(timings) = timings {
        timings
            .entry("runtime.boot_to_dispatch_s")
            .or_insert_with(|| json!(0.0));
        timings
            .entry("runtime.dispatch_s")
            .or_insert_with(|| json!(0.0));
        timings
            .entry("runtime.read_request_s")
            .or_insert_with(|| json!(0.0));
    }
}

fn emit_dispatch_audit(request: &Request, response: &Value, dispatch_s: f64) {
    if skip_dispatch_audit(&request.op) {
        return;
    }
    let total_ms = dispatch_s * 1000.0;
    let invocation_id = request
        .args
        .get("invocation_id")
        .and_then(Value::as_str)
        .unwrap_or(&request.invocation_id);
    let agent_id = request.args.get("agent_id").and_then(Value::as_str);
    let workspace_mode = response
        .get("workspace_mode")
        .or_else(|| response.get("workspace"))
        .and_then(Value::as_str)
        .map(str::to_owned);
    let exit_status = response
        .get("status")
        .and_then(Value::as_str)
        .or_else(|| {
            response
                .get("success")
                .and_then(Value::as_bool)
                .map(|success| if success { "ok" } else { "error" })
        })
        .unwrap_or("unknown");
    crate::audit_buffer::safe_emit(
        build_event(
            "tool_call.completed",
            "tool_call",
            json!({
                "tool_use_id": invocation_id,
                "tool_name": request.op,
                "agent_id": agent_id,
                "workspace_mode": workspace_mode,
                "duration_ms": total_ms,
                "total_ms": total_ms,
                "exit_status": exit_status,
                "phase_totals_rollup": response.get("timings").cloned().unwrap_or_else(|| json!({})),
            }),
        ),
        Lane::Normal,
    );

    emit_occ_audit(request, response);
    emit_workspace_lifecycle_audit(request, response, total_ms);
    emit_background_audit(request, response, total_ms);
}

fn skip_dispatch_audit(op: &str) -> bool {
    op.starts_with("api.audit.")
        || matches!(
            op,
            "api.runtime.ready"
                | "api.v1.heartbeat"
                | "api.v1.inflight_count"
                | "api.v1.pty_session_count"
        )
}

fn emit_occ_audit(request: &Request, response: &Value) {
    if !is_occ_op(&request.op) {
        return;
    }
    let changed_path_count = response
        .get("changed_paths")
        .and_then(Value::as_array)
        .map_or(0_i64, |paths| paths.len() as i64);
    let conflict = response.get("conflict").filter(|value| !value.is_null());
    let event_type = if conflict.is_some() {
        "occ.conflict"
    } else {
        "occ.publish"
    };
    let conflict_kind = conflict
        .and_then(|value| value.get("reason"))
        .and_then(Value::as_str)
        .or_else(|| response.get("conflict_reason").and_then(Value::as_str));
    crate::audit_buffer::safe_emit(
        build_event(
            event_type,
            "occ",
            json!({
                "operation_id": request.invocation_id,
                "changed_path_count": changed_path_count,
                "prepare_ms": timing_ms(response, "occ.prepare.total_s"),
                "apply_ms": timing_ms(response, "command_exec.occ_apply_s")
                    .or_else(|| timing_ms(response, "api.write.occ_apply_s"))
                    .or_else(|| timing_ms(response, "api.edit.occ_apply_s")),
                "commit_ms": timing_ms(response, "occ.commit.total_s"),
                "publish_layer_ms": timing_ms(response, "occ.commit.publish_layer_s"),
                "conflict_kind": conflict_kind,
                "conflict_path": conflict
                    .and_then(|value| value.get("conflict_file"))
                    .and_then(Value::as_str),
                "conflict_reason": response.get("conflict_reason").and_then(Value::as_str),
                "current_manifest_version": timing_i64(response, "resource.layer_stack.manifest_depth"),
            }),
        ),
        Lane::Normal,
    );
}

fn emit_workspace_lifecycle_audit(request: &Request, response: &Value, total_ms: f64) {
    if request.op == "api.layer_metrics" {
        crate::audit_buffer::safe_emit(
            build_event(
                "layer_stack.maintenance",
                "layer_stack",
                json!({
                    "operation_id": request.invocation_id,
                    "manifest_version": response.get("manifest_version").and_then(Value::as_i64),
                    "layer_count": response.get("manifest_depth").and_then(Value::as_i64),
                    "lease_hold_ms": total_ms,
                }),
            ),
            Lane::Normal,
        );
        return;
    }
    if !uses_overlay_or_lease(&request.op, response) {
        return;
    }
    crate::audit_buffer::safe_emit(
        build_event(
            "layer_stack.lease_released",
            "layer_stack",
            json!({
                "operation_id": request.invocation_id,
                "owner_request_id": request.invocation_id,
                "manifest_version": timing_i64(response, "resource.layer_stack.manifest_depth"),
                "layer_count": timing_i64(response, "resource.layer_stack.manifest_path_count"),
                "lease_hold_ms": total_ms,
            }),
        ),
        Lane::Normal,
    );
    crate::audit_buffer::safe_emit(
        build_event(
            "overlay_workspace.cleanup",
            "overlay_workspace",
            json!({
                "operation_id": request.invocation_id,
                "workspace_mode": response
                    .get("workspace_mode")
                    .or_else(|| response.get("workspace"))
                    .and_then(Value::as_str)
                    .unwrap_or("ephemeral"),
                "cleanup_ms": total_ms,
                "scratch_removed": true,
                "changed_path_count": response
                    .get("changed_paths")
                    .and_then(Value::as_array)
                    .map(|paths| paths.len() as i64),
            }),
        ),
        Lane::Normal,
    );
}

fn emit_background_audit(request: &Request, response: &Value, total_ms: f64) {
    let Some((event_type, task_kind)) = background_event_kind(request, response) else {
        return;
    };
    let pty_session_id = request
        .args
        .get("pty_session_id")
        .and_then(Value::as_str)
        .or_else(|| response.get("pty_session_id").and_then(Value::as_str))
        .unwrap_or(&request.invocation_id);
    crate::audit_buffer::safe_emit(
        build_event(
            event_type,
            "background_tool",
            json!({
                "background_task_id": pty_session_id,
                "task_kind": task_kind,
                "tool_name": request.op,
                "agent_id": request.args.get("agent_id").and_then(Value::as_str),
                "status": response.get("status").and_then(Value::as_str),
                "exit_code": response.get("exit_code").and_then(Value::as_i64),
                "duration_ms": total_ms,
            }),
        ),
        Lane::Normal,
    );
}

fn background_event_kind(
    request: &Request,
    response: &Value,
) -> Option<(&'static str, &'static str)> {
    match request.op.as_str() {
        "api.v1.exec_command"
            if request
                .args
                .get("tty")
                .and_then(Value::as_bool)
                .unwrap_or(false)
                && response.get("pty_session_id").is_some() =>
        {
            Some(("background_tool.started", "pty"))
        }
        "api.v1.exec_command"
            if request
                .args
                .get("tty")
                .and_then(Value::as_bool)
                .unwrap_or(false) =>
        {
            Some(("background_tool.completed", "pty"))
        }
        "api.v1.pty.write_stdin" => Some(("background_tool.input", "pty")),
        "api.v1.pty.progress" => Some(("background_tool.progress", "pty")),
        "api.v1.pty.cancel" => Some(("background_tool.cancelled", "pty")),
        "api.v1.pty.collect_completed" => Some(("background_tool.completed", "pty")),
        _ => None,
    }
}

fn is_occ_op(op: &str) -> bool {
    matches!(
        op,
        "api.write_file"
            | "api.v1.write_file"
            | "api.edit_file"
            | "api.v1.edit_file"
            | "api.v1.shell"
            | "api.v1.exec_command"
    )
}

fn uses_overlay_or_lease(op: &str, response: &Value) -> bool {
    if matches!(
        op,
        "api.glob"
            | "api.v1.glob"
            | "api.grep"
            | "api.v1.grep"
            | "api.v1.shell"
            | "api.v1.pty.cancel"
            | "api.v1.pty.collect_completed"
    ) {
        return true;
    }
    if op == "api.v1.exec_command" {
        return response
            .get("pty_session_id")
            .and_then(Value::as_str)
            .is_none();
    }
    false
}

fn timing_ms(response: &Value, key: &str) -> Option<f64> {
    timing_f64(response, key).map(|seconds| seconds * 1000.0)
}

fn timing_i64(response: &Value, key: &str) -> Option<i64> {
    timing_f64(response, key).map(|value| value.round() as i64)
}

fn timing_f64(response: &Value, key: &str) -> Option<f64> {
    response
        .get("timings")
        .and_then(Value::as_object)
        .and_then(|timings| timings.get(key))
        .and_then(Value::as_f64)
}

fn daemon_uptime_s() -> f64 {
    static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}

fn error_type(err: &DaemonError) -> &'static str {
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
    use std::sync::atomic::{AtomicU64, Ordering};

    use eos_protocol::audit::Lane;
    use serde_json::json;

    use super::*;

    #[test]
    fn shell_command_accepts_string_wire_shape() {
        let command = require_shell_command(&json!({"command": "echo hi"}))
            .expect("string shell command is valid");

        assert_eq!(command, json!("echo hi"));
    }

    #[test]
    fn shell_command_accepts_raw_argv_wire_shape() {
        let command = require_shell_command(&json!({"command": ["true"]}))
            .expect("argv shell command is valid");

        assert_eq!(command, json!(["true"]));
    }

    #[test]
    fn shell_command_rejects_empty_values() {
        assert!(require_shell_command(&json!({"command": []})).is_err());
        assert!(require_shell_command(&json!({"command": [""]})).is_err());
        assert!(require_shell_command(&json!({"command": [true]})).is_err());
    }

    #[test]
    fn op_table_rejects_different_handler_collision() {
        fn first_handler(
            _args: &Value,
            _context: DispatchContext<'_>,
        ) -> Result<Value, DaemonError> {
            Ok(json!({"handler": "first"}))
        }
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
    fn gated_stale_base_aborts_without_publish() {
        let fixture = Fixture::new("gated_stale");
        let old_hash = hash_bytes(b"# README\n");
        LayerStack::open(fixture.root.clone())
            .expect("open stack")
            .publish_layer(&[LayerChange::Write {
                path: lp("README.md"),
                content: b"# theirs\n".to_vec(),
            }])
            .expect("publish competing layer");

        let result = transaction(&fixture).revalidate_and_publish(&PreparedChangeset {
            snapshot_version: Some(1),
            path_groups: vec![publish_decision("README.md", Route::Gated, Some(old_hash))],
            changes: vec![LayerChange::Write {
                path: lp("README.md"),
                content: b"# mine\n".to_vec(),
            }],
            atomic: true,
        });

        let result = result.expect("validation returns regular result");
        assert_eq!(result.published_manifest_version, None);
        assert_eq!(result.files[0].status, OccStatus::AbortedVersion);
        assert_eq!(read_text(&fixture, "README.md"), "# theirs\n");
    }

    #[test]
    fn direct_route_ignores_stale_base_and_publishes() {
        let fixture = Fixture::new("direct_stale");
        LayerStack::open(fixture.root.clone())
            .expect("open stack")
            .publish_layer(&[LayerChange::Write {
                path: lp("target/out.txt"),
                content: b"theirs\n".to_vec(),
            }])
            .expect("publish competing layer");

        let result = transaction(&fixture).revalidate_and_publish(&PreparedChangeset {
            snapshot_version: Some(1),
            path_groups: vec![publish_decision(
                "target/out.txt",
                Route::Direct,
                Some("stale".to_owned()),
            )],
            changes: vec![LayerChange::Write {
                path: lp("target/out.txt"),
                content: b"mine\n".to_vec(),
            }],
            atomic: true,
        });

        let result = result.expect("direct route publishes");
        assert!(result.success());
        assert_eq!(result.files[0].status, OccStatus::Committed);
        assert_eq!(read_text(&fixture, "target/out.txt"), "mine\n");
    }

    #[test]
    fn atomic_mixed_validation_failure_drops_accepted_paths() {
        let fixture = Fixture::new("atomic_mixed");
        let old_hash = hash_bytes(b"# README\n");
        LayerStack::open(fixture.root.clone())
            .expect("open stack")
            .publish_layer(&[LayerChange::Write {
                path: lp("README.md"),
                content: b"# theirs\n".to_vec(),
            }])
            .expect("publish competing layer");

        let result = transaction(&fixture).revalidate_and_publish(&PreparedChangeset {
            snapshot_version: Some(1),
            path_groups: vec![
                publish_decision("README.md", Route::Gated, Some(old_hash)),
                publish_decision("target/out.txt", Route::Direct, None),
            ],
            changes: vec![
                LayerChange::Write {
                    path: lp("README.md"),
                    content: b"# mine\n".to_vec(),
                },
                LayerChange::Write {
                    path: lp("target/out.txt"),
                    content: b"ok\n".to_vec(),
                },
            ],
            atomic: true,
        });

        let result = result.expect("atomic validation returns result");
        assert_eq!(result.published_manifest_version, None);
        assert_eq!(result.files[0].status, OccStatus::AbortedVersion);
        assert_eq!(result.files[1].status, OccStatus::Dropped);
        assert_eq!(read_text(&fixture, "README.md"), "# theirs\n");
        assert!(
            !LayerStack::open(fixture.root.clone())
                .expect("open stack")
                .read_bytes("target/out.txt")
                .expect("read target")
                .1
        );
    }

    #[test]
    fn root_gitignore_routes_target_as_direct() {
        let fixture = Fixture::new_with_gitignore("gitignore_direct", "target/\n*.pyc\n");
        let provider = LayerStackRouteProvider {
            root: fixture.root.clone(),
        };

        assert!(provider
            .is_ignored(&lp("target/out.txt"))
            .expect("gitignore read succeeds"));
        assert!(provider
            .is_ignored(&lp("pkg/cache.pyc"))
            .expect("gitignore read succeeds"));
        assert!(!provider
            .is_ignored(&lp("src/main.rs"))
            .expect("gitignore read succeeds"));
    }

    #[test]
    fn occ_route_metrics_count_gated_and_direct_paths() {
        let fixture = Fixture::new_with_gitignore("route_metrics", "target/\n*.pyc\n");
        let metrics = occ_route_metrics(
            &fixture.root,
            &[
                LayerChange::Write {
                    path: lp("src/main.rs"),
                    content: b"tracked".to_vec(),
                },
                LayerChange::Write {
                    path: lp("target/out.txt"),
                    content: b"direct".to_vec(),
                },
                LayerChange::Write {
                    path: lp("pkg/cache.pyc"),
                    content: b"direct".to_vec(),
                },
                LayerChange::Write {
                    path: lp(".git/config"),
                    content: b"drop".to_vec(),
                },
            ],
        )
        .expect("route metrics read gitignore");

        assert_eq!(metrics.gated_path_count, 1);
        assert_eq!(metrics.direct_path_count, 2);
    }

    #[test]
    fn audit_pull_reads_shared_daemon_ring() {
        let marker = format!("phase3t-audit-test-{}", unique_suffix());
        let snapshot =
            op_audit_snapshot(&json!({}), DispatchContext::empty()).expect("snapshot response");
        let after_seq = snapshot["snapshot"]["daemon"]["next_seq"]
            .as_i64()
            .unwrap_or(0)
            - 1;
        crate::audit_buffer::safe_emit(
            json!({"type": marker, "payload": {"source": "unit-test"}}),
            Lane::Normal,
        );

        let pulled = op_audit_pull(
            &json!({"after_seq": after_seq, "limit": 128}),
            DispatchContext::empty(),
        )
        .expect("pull response");

        let events = pulled["events"].as_array().expect("events array");
        assert!(events
            .iter()
            .any(|event| event["type"].as_str() == Some(marker.as_str())));
    }

    #[test]
    fn occ_service_cache_is_bounded_lru() {
        clear_occ_service_cache_for_tests();
        let base = std::env::temp_dir().join(format!("eosd-occ-cache-{}", unique_suffix()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).expect("create cache test root");

        let first = base.join("root-000");
        for index in 0..=OCC_SERVICE_CACHE_MAX {
            let root = base.join(format!("root-{index:03}"));
            std::fs::create_dir_all(&root).expect("create root");
            let lookup = occ_service_for_root(&root).expect("create service");
            assert!(lookup.cache_created);
        }

        let snapshot = occ_service_cache_snapshot();
        assert_eq!(snapshot["capacity"], json!(OCC_SERVICE_CACHE_MAX));
        assert_eq!(snapshot["size"], json!(OCC_SERVICE_CACHE_MAX));
        assert_eq!(snapshot["evictions_total"], json!(1));

        let recreated = occ_service_for_root(&first).expect("recreate evicted service");
        assert!(!recreated.cache_hit);
        assert!(recreated.cache_created);
        assert_eq!(recreated.evicted_count, 1);

        clear_occ_service_cache_for_tests();
        let _ = std::fs::remove_dir_all(base);
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
    ) -> eos_occ::PublishDecision {
        eos_occ::PublishDecision {
            path: lp(path),
            route,
            base_hash,
            message: None,
        }
    }

    fn lp(path: &str) -> LayerPath {
        LayerPath::parse(path).expect("test path is valid")
    }

    fn read_text(fixture: &Fixture, path: &str) -> String {
        LayerStack::open(fixture.root.clone())
            .expect("open stack")
            .read_text(path)
            .expect("read text")
            .0
    }

    struct Fixture {
        base: PathBuf,
        root: PathBuf,
    }

    impl Fixture {
        fn new(label: &str) -> Self {
            Self::new_with_gitignore(label, "")
        }

        fn new_with_gitignore(label: &str, gitignore: &str) -> Self {
            static COUNTER: AtomicU64 = AtomicU64::new(0);
            let base = std::env::temp_dir().join(format!(
                "eosd-occ-{label}-{}-{}",
                std::process::id(),
                COUNTER.fetch_add(1, Ordering::Relaxed)
            ));
            let _ = std::fs::remove_dir_all(&base);
            let root = base.join("layer-stack");
            let layer = root.join("layers").join("B000001-base");
            std::fs::create_dir_all(&layer).expect("create base layer dir");
            std::fs::create_dir_all(root.join("staging")).expect("create staging dir");
            std::fs::write(layer.join("README.md"), "# README\n").expect("write read fixture");
            if !gitignore.is_empty() {
                std::fs::write(layer.join(".gitignore"), gitignore).expect("write gitignore");
            }
            std::fs::write(
                root.join("manifest.json"),
                serde_json::to_string_pretty(&json!({
                    "schema_version": 1,
                    "version": 1,
                    "layers": [{"layer_id": "B000001-base", "path": "layers/B000001-base"}],
                }))
                .expect("serialize manifest"),
            )
            .expect("write manifest");
            Self { base, root }
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.base);
        }
    }
}
