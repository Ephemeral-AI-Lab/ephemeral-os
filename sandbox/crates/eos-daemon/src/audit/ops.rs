//! Audit ring op handlers.

use serde_json::Value;

use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;

/// `api.audit.pull` — drain ring events after a cursor (backs the pull API).
// Op handlers share the fallible dispatcher ABI even when this handler only
// reads the in-memory audit ring.
#[expect(
    clippy::unnecessary_wraps,
    reason = "dispatcher handlers share a fallible ABI"
)]
pub(crate) fn op_audit_pull(
    args: &Value,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let after_seq = args.get("after_seq").and_then(Value::as_i64).unwrap_or(-1);
    let default_limit = context
        .audit_config()
        .map_or(1000, |config| config.pull_limit_default);
    let limit = args
        .get("limit")
        .and_then(Value::as_u64)
        .map_or(default_limit, u64_to_usize_saturating);
    let mut response = crate::audit::buffer::global_audit_buffer().pull(after_seq, limit);
    response["success"] = Value::Bool(true);
    Ok(response)
}

/// `api.audit.snapshot` — ring buffer + snapshot blocks, no events.
// Op handlers share the fallible dispatcher ABI even when this handler only
// snapshots the in-memory audit ring.
#[expect(
    clippy::unnecessary_wraps,
    reason = "dispatcher handlers share a fallible ABI"
)]
pub(crate) fn op_audit_snapshot(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let _ = args;
    let mut response = crate::audit::buffer::global_audit_buffer().snapshot();
    response["success"] = Value::Bool(true);
    Ok(response)
}

/// `api.audit.reset_floor` — gated by typed daemon audit config.
pub(crate) fn op_audit_reset_floor(
    args: &Value,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let _ = args;
    if context
        .audit_config()
        .is_some_and(|config| config.allow_floor_reset)
    {
        Ok(serde_json::json!({"success": true, "reset": true}))
    } else {
        Err(DaemonError::Forbidden(
            "audit floor reset is disabled".to_owned(),
        ))
    }
}

fn u64_to_usize_saturating(value: u64) -> usize {
    usize::try_from(value).unwrap_or(usize::MAX)
}
