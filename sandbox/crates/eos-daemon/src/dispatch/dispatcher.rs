//! Op routing and request validation. Built-ins resolve through the catalog
//! into [`crate::builtin::dispatch`]; `plugin.*` misses defer to the runtime
//! plugin registry.

#[cfg(test)]
use std::path::PathBuf;
use std::time::Instant;

use eos_operation::core::catalog::BuiltinOp;
use eos_operation::{OpRequest, RequestError};
use serde_json::{json, Value};

use crate::wire::{ErrorKind, Request};
#[cfg(test)]
use eos_layerstack::LayerStack;

use crate::builtin;
#[cfg(test)]
use crate::invocation_registry::InFlightRegistry;
use crate::op_adapter::plugin;
#[cfg(test)]
use crate::response::{insert_tree_resource_timings, resource_timings, TreeResourceStats};
use crate::DispatchContext;

#[must_use]
pub fn dispatch(request: &Request) -> Value {
    dispatch_with_context(request, DispatchContext::empty())
}

#[must_use]
pub fn dispatch_with_context(request: &Request, context: DispatchContext<'_>) -> Value {
    let dispatch_start = Instant::now();
    let boot_to_dispatch_s = daemon_uptime_s();
    let read_request_s = context.read_request_s().unwrap_or(0.0);
    let finalize =
        |response| finalize_response(response, boot_to_dispatch_s, dispatch_start, read_request_s);
    if request.op.trim().is_empty() {
        return finalize(error_response(
            ErrorKind::InvalidRequest,
            "op is required",
            json!({}),
        ));
    }
    if !request.args.is_object() {
        return finalize(error_response(
            ErrorKind::InvalidRequest,
            "args must be an object",
            json!({}),
        ));
    }
    let Some(op) = BuiltinOp::from_op_name(&request.op) else {
        return finalize(plugin_fallback_or_unknown(request, context));
    };
    let parsed = match OpRequest::parse(op, &request.args) {
        Ok(parsed) => parsed,
        Err(RequestError::Args(error)) => {
            return finalize(builtin::parse_error_response(op, error).into_wire())
        }
        Err(RequestError::NotDaemonServed(_)) => {
            return finalize(plugin_fallback_or_unknown(request, context));
        }
    };
    finalize(builtin::dispatch(parsed, context).into_wire())
}

fn plugin_fallback_or_unknown(request: &Request, context: DispatchContext<'_>) -> Value {
    if let Some(response) =
        plugin::dispatch_registered_op(&request.op, &request.invocation_id, &request.args, context)
    {
        return match response {
            Ok(response) => response,
            Err(err) => error_response(err.wire_kind(), &err.to_string(), json!({})),
        };
    }
    error_response(
        ErrorKind::UnknownOp,
        &format!("unknown op: {}", request.op),
        json!({"op": request.op}),
    )
}

fn finalize_response(
    mut response: Value,
    boot_to_dispatch_s: f64,
    dispatch_start: Instant,
    read_request_s: f64,
) -> Value {
    let dispatch_s = dispatch_start.elapsed().as_secs_f64();
    attach_runtime_timings(
        &mut response,
        boot_to_dispatch_s,
        dispatch_s,
        read_request_s,
    );
    response
}

#[must_use]
pub(crate) fn error_response(kind: ErrorKind, message: &str, details: Value) -> Value {
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
#[path = "../../tests/unit/dispatcher/mod.rs"]
mod tests;
