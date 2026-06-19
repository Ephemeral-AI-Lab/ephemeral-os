//! Builtin dispatch after removal of the legacy op adapter layer.

use protocol::catalog::BuiltinOp;
use serde_json::{json, Value};

use crate::response::error_envelope;
use crate::DispatchContext;

pub(crate) fn dispatch(op: BuiltinOp, _context: DispatchContext<'_>) -> Value {
    let op = op.contract().name;
    error_envelope(
        crate::wire::ErrorKind::InvalidRequest,
        format!("builtin op adapter removed: {op}"),
        json!({"op": op}),
    )
}
