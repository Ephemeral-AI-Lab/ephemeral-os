//! Bridge daemon wire requests to the `daemon_operation` registry.

use daemon_operation::{DaemonOperations, OperationRequest};
use serde_json::{json, Value};

use crate::wire::{ErrorKind, Request};

#[must_use]
pub(crate) fn dispatch(operations: &DaemonOperations, request: &Request) -> Value {
    if request.op.trim().is_empty() {
        return crate::dispatcher::error_response(
            ErrorKind::InvalidRequest,
            "op is required",
            json!({}),
        );
    }
    if !request.args.is_object() {
        return crate::dispatcher::error_response(
            ErrorKind::InvalidRequest,
            "args must be an object",
            json!({}),
        );
    }
    daemon_operation::dispatch_operation(
        operations,
        OperationRequest::new(&request.op, &request.invocation_id, &request.args),
    )
    .into_wire_value()
}
