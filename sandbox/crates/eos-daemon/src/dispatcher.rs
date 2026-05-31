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

use std::collections::HashMap;

use serde_json::{json, Value};

use eos_protocol::{ErrorKind, Request};

use crate::error::DaemonError;

/// Env gate for `api.audit.reset_floor` (must be `"true"`).
/// `// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:404 — EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET`
pub const AUDIT_ALLOW_FLOOR_RESET_ENV: &str = "EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET";

/// A synchronous op handler: decoded args -> response value.
///
/// The Python handlers are a mix of sync + async; the Rust dispatcher resolves
/// that at the call site. This skeleton models the registered routing surface
/// rather than each handler's async-ness.
/// `// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:37 — Handler = Callable[[dict], Any]`
pub type Handler = fn(&Value) -> Result<Value, DaemonError>;

/// The op routing table. Re-registering the SAME handler under an op is a no-op;
/// a DIFFERENT handler under a claimed op is rejected so peer collisions surface.
/// `// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:42-57 — register_op + OP_TABLE`
#[derive(Default)]
pub struct OpTable {
    handlers: HashMap<String, Handler>,
}

impl OpTable {
    /// Build the table pre-populated with the daemon-owned builtin ops this
    /// phase wires (NO `ping`).
    // PORT backend/src/sandbox/daemon/rpc/dispatcher.py:404-449 — _register_builtin_operations
    pub fn with_builtins() -> Self {
        let mut table = Self::default();
        // The real registration also folds in WORKSPACE_TOOL_OPS, the
        // isolated-workspace ops, plugin ops, and the layer-stack control
        // surface; this skeleton registers the daemon-owned ops the task names.
        table.register("api.runtime.ready", op_runtime_ready);
        table.register("api.v1.heartbeat", op_heartbeat);
        table.register("api.layer_metrics", op_layer_metrics);
        table.register("api.audit.pull", op_audit_pull);
        table.register("api.audit.snapshot", op_audit_snapshot);
        table.register("api.audit.reset_floor", op_audit_reset_floor);
        table
    }

    /// Register `handler` under `op`. Last-wins in this skeleton; the port-time
    /// impl reproduces the same-handler no-op / different-handler reject.
    // PORT backend/src/sandbox/daemon/rpc/dispatcher.py:42-57 — register_op (collision reject)
    pub fn register(&mut self, op: &str, handler: Handler) {
        self.handlers.insert(op.to_owned(), handler);
    }

    /// Route `request` to its handler, returning the response value or an error
    /// envelope value. Validates the envelope, runs the handler, and on an
    /// unknown op returns the `unknown_op` envelope.
    // PORT backend/src/sandbox/daemon/rpc/dispatcher.py:60-160 — dispatch_envelope_async core
    pub fn dispatch(&self, request: &Request) -> Value {
        let _ = &self.handlers;
        todo!("PORT dispatcher.py:60-160 — validate envelope, register in_flight (Drop guard), plugin gate via acquire_dispatch_slot, run handler, wrap failures as internal_error w/ error_id")
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
fn op_runtime_ready(args: &Value) -> Result<Value, DaemonError> {
    let _ = args;
    todo!("PORT builtin_operations.py:176-198 — require layer_stack_root, run the 3 probes, ready = all ok, attach daemon_pid/uptime_s/timings")
}

/// `api.v1.heartbeat` — touch `last_seen` for the given invocation ids.
// PORT backend/src/sandbox/daemon/builtin_operations.py:113-117 — heartbeat: registry.heartbeat(ids) -> {success, touched}
fn op_heartbeat(args: &Value) -> Result<Value, DaemonError> {
    let _ = args;
    todo!("PORT builtin_operations.py:113-117 — InFlightRegistry::heartbeat(invocation_ids) -> {{success:true, touched}}")
}

/// `api.layer_metrics` — summarize layer-stack storage + lease state for a root.
// PORT backend/src/sandbox/daemon/builtin_operations.py:132-170 — layer_metrics
fn op_layer_metrics(args: &Value) -> Result<Value, DaemonError> {
    let _ = args;
    todo!("PORT builtin_operations.py:132-170 — read active manifest + binding, count layer/staging dirs, orphans/missing, storage bytes")
}

/// `api.audit.pull` — drain ring events after a cursor (backs the pull API).
// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:413-421 — _audit_pull_handler
fn op_audit_pull(args: &Value) -> Result<Value, DaemonError> {
    let _ = args;
    todo!(
        "PORT dispatcher.py:413-421 — get_audit_buffer().pull(after_seq, limit), set success=true"
    )
}

/// `api.audit.snapshot` — ring buffer + snapshot blocks, no events.
// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:423-428 — _audit_snapshot_handler
fn op_audit_snapshot(args: &Value) -> Result<Value, DaemonError> {
    let _ = args;
    todo!("PORT dispatcher.py:423-428 — get_audit_buffer().snapshot(), set success=true")
}

/// `api.audit.reset_floor` — gated behind [`AUDIT_ALLOW_FLOOR_RESET_ENV`].
// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:430-438 — _audit_reset_floor_handler (env gate -> forbidden)
fn op_audit_reset_floor(args: &Value) -> Result<Value, DaemonError> {
    let _ = (args, AUDIT_ALLOW_FLOOR_RESET_ENV);
    todo!("PORT dispatcher.py:430-438 — require EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET=true else forbidden envelope")
}
