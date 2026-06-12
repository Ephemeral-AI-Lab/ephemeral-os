//! Op routing and request validation. Built-ins resolve through the catalog
//! into [`crate::builtin::dispatch`]; `plugin.*` misses defer to the runtime
//! plugin registry.

#[cfg(test)]
use std::path::PathBuf;
use std::time::Instant;

use eos_operation::core::catalog::BuiltinOp;
use eos_operation::{OpRequest, OpResponse, OpResponseError, OpResponseErrorKind, RequestError};
use serde_json::{json, Value};

use crate::wire::{ErrorKind, Request};
#[cfg(test)]
use eos_layerstack::LayerStack;

use crate::builtin;
#[cfg(test)]
use crate::invocation_registry::InFlightRegistry;
use crate::op_adapter::{is_operation_envelope, ok_envelope, plugin};
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
    let finalize = |response| {
        finalize_response(
            response,
            &request.op,
            &request.invocation_id,
            boot_to_dispatch_s,
            dispatch_start,
            read_request_s,
        )
    };
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
            Ok(response) => ok_envelope(response),
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
    op: &str,
    invocation_id: &str,
    boot_to_dispatch_s: f64,
    dispatch_start: Instant,
    read_request_s: f64,
) -> Value {
    let dispatch_s = dispatch_start.elapsed().as_secs_f64();
    attach_runtime_observations(
        &mut response,
        op,
        invocation_id,
        boot_to_dispatch_s,
        dispatch_s,
        read_request_s,
    );
    response
}

#[must_use]
pub(crate) fn error_response(kind: ErrorKind, message: &str, details: Value) -> Value {
    OpResponse::Error(OpResponseError::new(
        response_error_kind(kind),
        message,
        details,
    ))
    .into_wire()
}

fn attach_runtime_observations(
    response: &mut Value,
    op: &str,
    invocation_id: &str,
    boot_to_dispatch_s: f64,
    dispatch_s: f64,
    read_request_s: f64,
) {
    let envelope = is_operation_envelope(response);
    let Some(obj) = response.as_object_mut() else {
        return;
    };
    if envelope {
        attach_envelope_runtime_meta(obj, op, invocation_id, dispatch_s);
        return;
    }
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

fn attach_envelope_runtime_meta(
    object: &mut serde_json::Map<String, Value>,
    op: &str,
    invocation_id: &str,
    dispatch_s: f64,
) {
    let meta = object
        .entry("meta".to_owned())
        .or_insert_with(|| json!({}))
        .as_object_mut();
    let Some(meta) = meta else {
        return;
    };
    if !op.is_empty() {
        fill_empty_string(meta, "op", op);
    }
    if !invocation_id.is_empty() {
        fill_empty_string(meta, "request_id", invocation_id);
    }
    if meta
        .get("duration_ms")
        .and_then(Value::as_f64)
        .is_none_or(|duration_ms| duration_ms <= 0.0)
    {
        meta.insert("duration_ms".to_owned(), json!(dispatch_s * 1000.0));
    }
    if meta
        .get("steps")
        .and_then(Value::as_array)
        .is_none_or(Vec::is_empty)
    {
        meta.insert(
            "steps".to_owned(),
            json!([{
                "kind": "runtime.dispatch",
                "duration_us": seconds_to_us(dispatch_s),
                "status": "ok",
            }]),
        );
    }
}

fn fill_empty_string(meta: &mut serde_json::Map<String, Value>, key: &str, value: &str) {
    if meta
        .get(key)
        .and_then(Value::as_str)
        .is_none_or(str::is_empty)
    {
        meta.insert(key.to_owned(), Value::String(value.to_owned()));
    }
}

fn seconds_to_us(seconds: f64) -> u64 {
    if !seconds.is_finite() || seconds <= 0.0 {
        return 0;
    }
    let micros = seconds * 1_000_000.0;
    if micros >= u64::MAX as f64 {
        u64::MAX
    } else {
        micros as u64
    }
}

fn response_error_kind(kind: ErrorKind) -> OpResponseErrorKind {
    match kind {
        ErrorKind::InvalidRequest => OpResponseErrorKind::InvalidRequest,
        ErrorKind::BadJson => OpResponseErrorKind::BadJson,
        ErrorKind::RequestTooLarge => OpResponseErrorKind::RequestTooLarge,
        ErrorKind::Unauthorized => OpResponseErrorKind::Unauthorized,
        ErrorKind::UnknownOp => OpResponseErrorKind::UnknownOp,
        ErrorKind::InternalError => OpResponseErrorKind::InternalError,
        ErrorKind::Forbidden => OpResponseErrorKind::Forbidden,
        ErrorKind::ForbiddenInIsolatedWorkspace => {
            OpResponseErrorKind::ForbiddenInIsolatedWorkspace
        }
        ErrorKind::LifecycleInProgress => OpResponseErrorKind::LifecycleInProgress,
    }
}

pub(crate) fn daemon_uptime_s() -> f64 {
    static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}

#[cfg(test)]
#[path = "../../tests/unit/dispatcher/mod.rs"]
mod tests;
