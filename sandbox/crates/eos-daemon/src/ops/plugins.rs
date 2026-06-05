//! Plugin daemon operation handlers and dynamic route dispatch.

use serde_json::Value;

use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;

pub(crate) fn op_ensure(args: &Value, context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    crate::services::plugins::op_ensure(args, context)
}

pub(crate) fn op_status(args: &Value, context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    crate::services::plugins::op_status(args, context)
}

pub(crate) fn dispatch_registered_op(
    op: &str,
    invocation_id: &str,
    args: &Value,
    context: DispatchContext<'_>,
) -> Option<Result<Value, DaemonError>> {
    crate::services::plugins::dispatch_registered_op(op, invocation_id, args, context)
}
