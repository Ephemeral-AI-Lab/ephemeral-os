//! Audit ring op handlers.

use serde_json::Value;

use crate::dispatcher::{DispatchContext, AUDIT_ALLOW_FLOOR_RESET_ENV};
use crate::error::DaemonError;
use crate::response_timings::u64_to_usize_saturating;

/// `api.audit.pull` — drain ring events after a cursor (backs the pull API).
// Op handlers share the fallible dispatcher ABI even when this handler only
// reads the in-memory audit ring.
#[expect(
    clippy::unnecessary_wraps,
    reason = "dispatcher handlers share a fallible ABI"
)]
pub(crate) fn op_audit_pull(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let after_seq = args.get("after_seq").and_then(Value::as_i64).unwrap_or(-1);
    let limit = args
        .get("limit")
        .and_then(Value::as_u64)
        .map_or(1000, u64_to_usize_saturating);
    let mut response = crate::audit_buffer::global_audit_buffer().pull(after_seq, limit);
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
    let mut response = crate::audit_buffer::global_audit_buffer().snapshot();
    response["success"] = Value::Bool(true);
    Ok(response)
}

/// `api.audit.reset_floor` — gated behind [`AUDIT_ALLOW_FLOOR_RESET_ENV`].
pub(crate) fn op_audit_reset_floor(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let _ = args;
    if std::env::var(AUDIT_ALLOW_FLOOR_RESET_ENV).is_ok_and(|raw| raw == "true") {
        Ok(serde_json::json!({"success": true, "reset": true}))
    } else {
        Err(DaemonError::Forbidden(
            "audit floor reset is disabled".to_owned(),
        ))
    }
}
