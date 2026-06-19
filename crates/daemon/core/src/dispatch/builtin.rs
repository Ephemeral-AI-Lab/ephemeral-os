//! Builtin dispatch after removal of the legacy op adapter layer.

use operation::{ArgsError, OpRequest};
use protocol::catalog::{BuiltinOp, OpFamily};
use serde_json::{json, Value};

use crate::response::{error_envelope, rejected_fault_envelope};
use crate::DispatchContext;

pub(crate) fn dispatch(request: OpRequest, _context: DispatchContext<'_>) -> Value {
    let op = op_request_name(&request);
    error_envelope(
        crate::wire::ErrorKind::InvalidRequest,
        format!("builtin op adapter removed: {op}"),
        json!({"op": op}),
    )
}

/// The per-family parse-failure channel: workspace families refuse in-band,
/// every other family answers with a structured `invalid_request` error
/// response.
pub(crate) fn parse_error_response(op: BuiltinOp, error: ArgsError) -> Value {
    match op.contract().family {
        OpFamily::IsolatedNetwork | OpFamily::WorkspaceRun => rejected_fault_envelope(
            "invalid_argument",
            error.message(),
            json!({"key": error.key}),
        ),
        _ => error_envelope(
            crate::wire::ErrorKind::InvalidRequest,
            format!("invalid request: {}", error.message()),
            json!({"message": error.message()}),
        ),
    }
}

fn op_request_name(request: &OpRequest) -> &'static str {
    match request {
        OpRequest::RuntimeReady(_) => "runtime_ready",
        OpRequest::InvocationHeartbeat(_) => "invocation_heartbeat",
        OpRequest::InvocationCancel(_) => "invocation_cancel",
        OpRequest::InflightCount(_) => "inflight_count",
        OpRequest::TraceExport(_) => "trace_export",
        OpRequest::TraceExportAck(_) => "trace_export_ack",
        OpRequest::LayerMetrics(_) => "layer_metrics",
        OpRequest::BuildWorkspaceBase(_) => "build_workspace_base",
        OpRequest::CommitToWorkspace(_) => "commit_to_workspace",
        OpRequest::CommitToGit(_) => "commit_to_git",
        OpRequest::WorkspaceBinding(_) => "workspace_binding",
        OpRequest::ReadFile(_) => "read_file",
        OpRequest::WriteFile(_) => "write_file",
        OpRequest::EditFile(_) => "edit_file",
        OpRequest::PluginList(_) => "plugin_list",
        OpRequest::PluginHealth(_) => "plugin_health",
        OpRequest::PyrightLspQuerySymbols(_) => "pyright_lsp_query_symbols",
        OpRequest::PyrightLspDefinition(_) => "pyright_lsp_definition",
        OpRequest::PyrightLspReferences(_) => "pyright_lsp_references",
        OpRequest::PyrightLspDiagnostics(_) => "pyright_lsp_diagnostics",
        OpRequest::IsolatedNetworkEnter(_) => "isolated_network_enter",
        OpRequest::IsolatedNetworkExit(_) => "isolated_network_exit",
        OpRequest::IsolatedNetworkStatus(_) => "isolated_network_status",
        OpRequest::IsolatedNetworkListOpen => "isolated_network_list_open",
        OpRequest::IsolatedNetworkTestReset => "isolated_network_test_reset",
        OpRequest::IsolatedNetworkTestCompactRemount(_) => "isolated_network_test_compact_remount",
        OpRequest::ExecCommand(_) => "exec_command",
        OpRequest::WriteStdin(_) => "write_stdin",
        OpRequest::CommandReadProgress(_) => "command_read_progress",
        OpRequest::CommandCancel(_) => "command_cancel",
        OpRequest::CommandCollectCompleted(_) => "command_collect_completed",
        OpRequest::CommandCount(_) => "command_count",
        OpRequest::CancelWorkspaceRunsByCaller(_) => "cancel_workspace_runs_by_caller",
        OpRequest::CancelWorkspaceRuns(_) => "cancel_workspace_runs",
    }
}
