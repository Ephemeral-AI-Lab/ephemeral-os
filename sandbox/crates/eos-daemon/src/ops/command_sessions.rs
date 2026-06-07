//! Command-session daemon operation handlers.

use serde_json::Value;

use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;

pub(crate) fn op_exec_command(
    args: &Value,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    crate::services::command_session::op_exec_command(args, context)
}

pub(crate) fn op_command_write_stdin(
    args: &Value,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    crate::services::command_session::op_command_write_stdin(args, context)
}

pub(crate) fn op_command_read_progress(
    args: &Value,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    crate::services::command_session::op_command_read_progress(args, context)
}

pub(crate) fn op_command_cancel(
    args: &Value,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    crate::services::command_session::op_command_cancel(args, context)
}

pub(crate) fn op_command_collect_completed(
    args: &Value,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    crate::services::command_session::op_command_collect_completed(args, context)
}

pub(crate) fn op_command_session_count(
    args: &Value,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    crate::services::command_session::op_command_session_count(args, context)
}
