//! Isolated-workspace daemon operation handlers.

use serde_json::Value;

use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;

pub(crate) fn op_enter(args: &Value, context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    crate::services::isolated_workspace::op_enter(args, context)
}

pub(crate) fn op_exit(args: &Value, context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    crate::services::isolated_workspace::op_exit(args, context)
}

pub(crate) fn op_status(args: &Value, context: DispatchContext<'_>) -> Result<Value, DaemonError> {
    crate::services::isolated_workspace::op_status(args, context)
}

pub(crate) fn op_list_open(
    args: &Value,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    crate::services::isolated_workspace::op_list_open(args, context)
}

pub(crate) fn op_test_reset(
    args: &Value,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    crate::services::isolated_workspace::op_test_reset(args, context)
}
