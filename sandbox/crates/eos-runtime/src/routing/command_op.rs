//! Command operation routing between isolated and ephemeral workspace targets.

use std::path::PathBuf;

use eos_command_ops::{command_ops, command_session_scratch_root, ExecTarget};
use eos_command_session::{CommandResponse, CommandSessionError, StartCommandSession};
use thiserror::Error;

use crate::WorkspaceRuntime;

/// Typed command start request after daemon JSON parsing.
pub struct ExecCommandRequest {
    pub invocation_id: String,
    pub caller_id: String,
    pub cmd: String,
    pub layer_stack_root: Option<PathBuf>,
    pub timeout_seconds: Option<f64>,
    pub yield_time_ms: u64,
}

/// Errors from routing or starting a workspace-bound command.
#[derive(Debug, Error)]
pub enum CommandOpError {
    #[error("layer_stack_root is required")]
    MissingLayerStackRoot,
    #[error(transparent)]
    LayerStack(#[from] eos_layerstack::LayerStackError),
    #[error(transparent)]
    Command(#[from] CommandSessionError),
}

/// Start a command on the caller's active isolated workspace, or on the direct
/// ephemeral layer-stack path when no isolated workspace is open.
///
/// # Errors
///
/// Returns [`CommandOpError`] when direct routing lacks a layer-stack root,
/// when the root has no workspace binding, or when command startup fails.
pub fn exec_command(
    workspace: Option<&WorkspaceRuntime>,
    request: ExecCommandRequest,
) -> Result<CommandResponse, CommandOpError> {
    let ExecCommandRequest {
        invocation_id,
        caller_id,
        cmd,
        layer_stack_root,
        timeout_seconds,
        yield_time_ms,
    } = request;

    if let Some(binding) = workspace.and_then(|workspace| workspace.command_binding_for(&caller_id))
    {
        return command_ops()
            .exec_command(
                StartCommandSession {
                    invocation_id,
                    caller_id: binding.caller_id.clone(),
                    cmd,
                    timeout_seconds,
                    yield_time_ms,
                },
                ExecTarget::Isolated {
                    binding: Box::new(binding),
                },
            )
            .map_err(CommandOpError::Command);
    }

    let root = layer_stack_root.ok_or(CommandOpError::MissingLayerStackRoot)?;
    let binding = eos_layerstack::require_workspace_binding(&root)?;
    command_ops()
        .exec_command(
            StartCommandSession {
                invocation_id,
                caller_id,
                cmd,
                timeout_seconds,
                yield_time_ms,
            },
            ExecTarget::Ephemeral {
                root,
                workspace_root: PathBuf::from(binding.workspace_root),
                scratch_root: command_session_scratch_root(),
            },
        )
        .map_err(CommandOpError::Command)
}
