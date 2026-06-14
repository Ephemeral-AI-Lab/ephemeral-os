//! Op routing and request validation. Built-ins resolve through the catalog
//! into [`crate::builtin::dispatch`]; `plugin.*` misses defer to the runtime
//! plugin registry.

#[cfg(test)]
use std::path::PathBuf;
use std::time::Instant;

use operation::OpRequest;
use operation::RequestError;
use protocol::catalog::BuiltinOp;
use serde_json::{json, Value};

use crate::wire::{ErrorKind, Request};
#[cfg(test)]
use layerstack::LayerStack;

use crate::builtin;
#[cfg(test)]
use crate::invocation_registry::InFlightRegistry;
use crate::op_adapter::{error_envelope, ok_envelope, plugin};
#[cfg(test)]
use crate::response::{insert_tree_resource_timings, resource_timings, TreeResourceStats};
use crate::DispatchContext;

#[must_use]
pub fn dispatch(request: &Request) -> Value {
    dispatch_with_context(request, DispatchContext::empty())
}

#[must_use]
pub fn dispatch_with_context(request: &Request, context: DispatchContext<'_>) -> Value {
    // Dispatch returns the typed envelope; response `meta` (op, request_id,
    // duration, steps, route, resources) is owned solely by the span-derived
    // stamp on the transport path (`trace::stamp_pending_envelope_meta`). The
    // dispatcher never hand-maintains a parallel meta map.
    if request.op.trim().is_empty() {
        return error_response(ErrorKind::InvalidRequest, "op is required", json!({}));
    }
    if !request.args.is_object() {
        return error_response(
            ErrorKind::InvalidRequest,
            "args must be an object",
            json!({}),
        );
    }
    let Some(op) = BuiltinOp::from_op_name(&request.op) else {
        return plugin_fallback_or_unknown(request, context);
    };
    let parsed = match OpRequest::parse(op, &request.args, &request.invocation_id) {
        Ok(parsed) => parsed,
        Err(RequestError::Args(error)) => return builtin::parse_error_response(op, error),
        Err(RequestError::NotDaemonServed(_)) => {
            return error_response(
                ErrorKind::Forbidden,
                format!("op {} is served by the host gateway", request.op),
                json!({"op": request.op, "served_by": "host"}),
            );
        }
    };
    builtin::dispatch(parsed, context)
}

fn plugin_fallback_or_unknown(request: &Request, context: DispatchContext<'_>) -> Value {
    if let Some(response) =
        plugin::dispatch_registered_op(&request.op, &request.invocation_id, &request.args, context)
    {
        return match response {
            Ok(response) => ok_envelope(response),
            Err(err) => error_response(err.wire_kind(), err.to_string(), json!({})),
        };
    }
    error_response(
        ErrorKind::UnknownOp,
        format!("unknown op: {}", request.op),
        json!({"op": request.op}),
    )
}

#[must_use]
pub(crate) fn error_response(kind: ErrorKind, message: impl Into<String>, details: Value) -> Value {
    error_envelope(kind, message, details)
}

pub(crate) fn daemon_uptime_s() -> f64 {
    static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}

#[cfg(test)]
#[path = "../../tests/unit/dispatcher/mod.rs"]
mod tests;
