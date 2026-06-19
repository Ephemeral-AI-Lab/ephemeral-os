use std::time::Duration;

use anyhow::Result;
use serde_json::{json, Value};

use super::ForwardTraceContext;

pub(crate) fn host_ok_response(op: &str, trace: &ForwardTraceContext, result: Value) -> Value {
    json!({
        "status": "ok",
        "result": result,
        "meta": host_response_meta(op, trace),
    })
}

pub(crate) fn host_error_response(
    op: &str,
    trace: &ForwardTraceContext,
    kind: &str,
    message: &str,
) -> Value {
    json!({
        "status": "error",
        "error": {
            "kind": kind,
            "message": message,
            "details": {},
        },
        "meta": host_response_meta(op, trace),
    })
}

fn host_response_meta(op: &str, trace: &ForwardTraceContext) -> Value {
    json!({
        "op": op,
        "request_id": trace.request_id.as_str(),
        "trace": {
            "trace_id": trace.trace_id.as_str(),
            "request_id": trace.request_id.as_str(),
            "store": "local_sqlite",
            "degraded": false,
        },
    })
}

pub(crate) fn duration_ms(duration: Duration) -> u64 {
    u64::try_from(duration.as_millis()).unwrap_or(u64::MAX)
}

pub(crate) fn host_result_summary(result: &Result<Value>) -> Value {
    match result {
        Ok(value) => json!({"status": "ok", "result": value}),
        Err(err) => json!({"status": "error", "message": err.to_string()}),
    }
}
