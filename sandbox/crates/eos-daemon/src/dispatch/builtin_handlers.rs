//! Built-in daemon op handler mapping.

use eos_operation::core::ops::BuiltinOp;

use crate::dispatcher::Handler;
use crate::op_adapter::{checkpoint, command, control, files, isolation, plugin, workspace_run};

pub(crate) const fn builtin_handler(op: BuiltinOp) -> Option<Handler> {
    Some(match op {
        BuiltinOp::SandboxAcquire
        | BuiltinOp::SandboxRelease
        | BuiltinOp::SandboxStatus
        | BuiltinOp::SandboxList => return None,
        BuiltinOp::RuntimeReady => control::op_runtime_ready,
        BuiltinOp::InvocationHeartbeat => control::op_heartbeat,
        BuiltinOp::InvocationCancel => control::op_cancel,
        BuiltinOp::InflightCount => control::op_inflight_count,
        BuiltinOp::LayerMetrics => checkpoint::layer_metrics,
        BuiltinOp::EnsureWorkspaceBase => checkpoint::ensure_workspace_base,
        BuiltinOp::BuildWorkspaceBase => checkpoint::build_workspace_base,
        BuiltinOp::CommitToWorkspace => checkpoint::commit_to_workspace,
        BuiltinOp::CommitToGit => checkpoint::commit_to_git,
        BuiltinOp::WorkspaceBinding => checkpoint::workspace_binding,
        BuiltinOp::ReadFile => files::op_read_file,
        BuiltinOp::WriteFile => files::op_write_file,
        BuiltinOp::EditFile => files::op_edit_file,
        BuiltinOp::PluginEnsure => plugin::op_ensure,
        BuiltinOp::PluginStatus => plugin::op_status,
        BuiltinOp::IsolatedWorkspaceEnter => isolation::op_enter,
        BuiltinOp::IsolatedWorkspaceExit => isolation::op_exit,
        BuiltinOp::IsolatedWorkspaceStatus => isolation::op_status,
        BuiltinOp::IsolatedWorkspaceListOpen => isolation::op_list_open,
        BuiltinOp::IsolatedWorkspaceTestReset => isolation::op_test_reset,
        BuiltinOp::ExecCommand => command::op_exec_command,
        BuiltinOp::WriteStdin => command::command_session_write_stdin,
        BuiltinOp::CommandReadProgress => command::command_session_read_progress,
        BuiltinOp::CommandCancel => command::command_session_cancel,
        BuiltinOp::CommandCollectCompleted => command::op_command_collect_completed,
        BuiltinOp::CommandSessionCount => command::op_command_session_count,
        BuiltinOp::CancelWorkspaceRunsByCaller => {
            workspace_run::op_cancel_workspace_runs_by_caller_id
        }
        BuiltinOp::CancelWorkspaceRuns => workspace_run::op_cancel_workspace_runs,
        _ => return None,
    })
}
