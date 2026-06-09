//! Checkpoint operation handlers.

use serde_json::Value;

use crate::adapters::checkpoint;
use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;

/// `api.layer_metrics` — summarize layer-stack storage + lease state for a root.
pub(crate) fn op_layer_metrics(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    checkpoint::layer_metrics(args)
}

pub(crate) fn op_build_workspace_base(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    checkpoint::build_workspace_base(args)
}

pub(crate) fn op_ensure_workspace_base(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    checkpoint::ensure_workspace_base(args)
}

pub(crate) fn op_workspace_binding(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    checkpoint::workspace_binding(args)
}

pub(crate) fn op_commit_to_workspace(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    checkpoint::commit_to_workspace(args)
}

pub(crate) fn op_commit_to_git(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    checkpoint::commit_to_git(args)
}
