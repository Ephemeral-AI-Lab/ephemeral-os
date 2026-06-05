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
//! (floor-reset gated by typed daemon audit config). The full op table
//! (workspace-tool, isolated-workspace, plugin, layer-stack control) folds in
//! at port time through the same routing.

use std::collections::HashMap;
#[cfg(test)]
use std::path::PathBuf;
use std::time::Instant;

use serde_json::{json, Value};

#[cfg(test)]
use eos_layerstack::LayerStack;
use eos_protocol::{ops as protocol_ops, ErrorKind, Request};
#[cfg(test)]
use eos_protocol::{LayerChange, LayerPath};

use crate::audit::events::emit_dispatch_audit;
#[cfg(test)]
use crate::audit::events::{background_event_kind, emit_auto_squash_audit, uses_overlay_or_lease};
#[cfg(test)]
use crate::audit::ops::{op_audit_pull, op_audit_snapshot};
use crate::config::AuditConfig;
use crate::error::DaemonError;
use crate::invocation_registry::InFlightRegistry;
#[cfg(test)]
use crate::occ_writer::{
    base_hashes_for_snapshot, hash_bytes, normalize_root_key, occ_route_metrics,
    LayerStackCommitTransaction, LayerStackRouteProvider, OccServiceCache, OCC_SERVICE_CACHE_MAX,
};
use crate::ops::{checkpoint, runtime};
#[cfg(test)]
use crate::response_timings::{
    i64_to_f64_saturating, insert_tree_resource_timings, resource_timings, TreeResourceStats,
};
#[cfg(test)]
use eos_occ::{
    CommitQueue, CommitTransactionPort, OccRouteProvider, OccService, OccStatus, PreparedChangeset,
    Route,
};

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
    audit_config: Option<&'ctx AuditConfig>,
    read_request_s: Option<f64>,
}

impl<'ctx> DispatchContext<'ctx> {
    /// Empty context for direct unit dispatch.
    #[must_use]
    pub const fn empty() -> Self {
        Self {
            invocation_registry: None,
            audit_config: None,
            read_request_s: None,
        }
    }

    /// Context carrying the server's invocation registry.
    #[must_use]
    pub const fn with_invocation_registry(invocation_registry: &'ctx InFlightRegistry) -> Self {
        Self {
            invocation_registry: Some(invocation_registry),
            audit_config: None,
            read_request_s: None,
        }
    }

    /// Context carrying the server's invocation registry, audit config, and
    /// measured request read duration.
    #[must_use]
    pub const fn with_runtime_config(
        invocation_registry: &'ctx InFlightRegistry,
        audit_config: &'ctx AuditConfig,
        read_request_s: f64,
    ) -> Self {
        Self {
            invocation_registry: Some(invocation_registry),
            audit_config: Some(audit_config),
            read_request_s: Some(read_request_s),
        }
    }

    pub(crate) const fn invocation_registry(&self) -> Option<&'ctx InFlightRegistry> {
        self.invocation_registry
    }

    pub(crate) const fn audit_config(&self) -> Option<&'ctx AuditConfig> {
        self.audit_config
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
        table.register_builtin(protocol_ops::API_RUNTIME_READY, runtime::op_runtime_ready);
        table.register_builtin(protocol_ops::API_V1_CANCEL, runtime::op_cancel);
        table.register_builtin(protocol_ops::API_V1_HEARTBEAT, runtime::op_heartbeat);
        table.register_builtin(
            protocol_ops::API_V1_INFLIGHT_COUNT,
            runtime::op_inflight_count,
        );
        table.register_builtin(
            protocol_ops::API_LAYER_METRICS,
            checkpoint::op_layer_metrics,
        );
        table.register_builtin(
            protocol_ops::API_ENSURE_WORKSPACE_BASE,
            checkpoint::op_ensure_workspace_base,
        );
        table.register_builtin(
            protocol_ops::API_BUILD_WORKSPACE_BASE,
            checkpoint::op_build_workspace_base,
        );
        table.register_builtin(
            protocol_ops::API_COMMIT_TO_WORKSPACE,
            checkpoint::op_commit_to_workspace,
        );
        table.register_builtin(
            protocol_ops::API_COMMIT_TO_GIT,
            checkpoint::op_commit_to_git,
        );
        table.register_builtin(
            protocol_ops::API_WORKSPACE_BINDING,
            checkpoint::op_workspace_binding,
        );
        table.register_builtin(
            protocol_ops::API_AUDIT_PULL,
            crate::audit::ops::op_audit_pull,
        );
        table.register_builtin(
            protocol_ops::API_AUDIT_SNAPSHOT,
            crate::audit::ops::op_audit_snapshot,
        );
        table.register_builtin(
            protocol_ops::API_AUDIT_RESET_FLOOR,
            crate::audit::ops::op_audit_reset_floor,
        );
        table.register_builtin(
            protocol_ops::API_V1_READ_FILE,
            crate::workspace_ops::op_read_file,
        );
        table.register_builtin(
            protocol_ops::API_V1_WRITE_FILE,
            crate::workspace_ops::op_write_file,
        );
        table.register_builtin(
            protocol_ops::API_V1_EDIT_FILE,
            crate::workspace_ops::op_edit_file,
        );
        table.register_builtin(protocol_ops::API_PLUGIN_ENSURE, crate::plugin::op_ensure);
        table.register_builtin(protocol_ops::API_PLUGIN_STATUS, crate::plugin::op_status);
        table.register_builtin(
            protocol_ops::API_ISOLATED_WORKSPACE_ENTER,
            crate::isolated::op_enter,
        );
        table.register_builtin(
            protocol_ops::API_ISOLATED_WORKSPACE_EXIT,
            crate::isolated::op_exit,
        );
        table.register_builtin(
            protocol_ops::API_ISOLATED_WORKSPACE_STATUS,
            crate::isolated::op_status,
        );
        table.register_builtin(
            protocol_ops::API_ISOLATED_WORKSPACE_LIST_OPEN,
            crate::isolated::op_list_open,
        );
        table.register_builtin(
            protocol_ops::API_ISOLATED_WORKSPACE_TEST_RESET,
            crate::isolated::op_test_reset,
        );
        table.register_builtin(
            protocol_ops::API_V1_EXEC_COMMAND,
            crate::command::op_exec_command,
        );
        table.register_builtin(
            protocol_ops::API_V1_WRITE_STDIN,
            crate::command::op_command_write_stdin,
        );
        table.register_builtin(
            protocol_ops::API_V1_COMMAND_CANCEL,
            crate::command::op_command_cancel,
        );
        table.register_builtin(
            protocol_ops::API_V1_COMMAND_COLLECT_COMPLETED,
            crate::command::op_command_collect_completed,
        );
        table.register_builtin(
            protocol_ops::API_V1_COMMAND_SESSION_COUNT,
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

pub(crate) fn daemon_uptime_s() -> f64 {
    static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}

#[cfg(test)]
#[path = "../tests/dispatcher/mod.rs"]
mod tests;
