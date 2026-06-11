//! Command-session dispatcher handlers, driving the caller-keyed
//! command runtime in `eos_operation::command`.

use std::path::PathBuf;

use eos_command_session::{
    CancelCommandSession, CollectCompleted, CommandSessionError, ReadCommandProgress,
    StartCommandSession, WriteStdin,
};
use eos_operation::command::contract::{
    CancelCommandInput, CollectCompletedInput, CommandResponse, CommandSessionCountOutput,
    CommandStatus, ExecCommandInput, ReadProgressInput, WriteStdinInput,
};
use eos_operation::command::{
    command_ops, command_session_config, command_session_scratch_root, ExecTarget,
};
use eos_operation::control::contract::CallerCountInput;
use serde_json::Value;
use thiserror::Error;

use crate::error::DaemonError;
use crate::response::u64_to_f64_saturating;
use crate::{DispatchContext, WorkspaceRuntime};

use super::to_wire_value;

/// Typed command start request after daemon JSON parsing.
struct ExecCommandRequest {
    invocation_id: String,
    caller_id: String,
    cmd: String,
    layer_stack_root: Option<PathBuf>,
    timeout_seconds: Option<f64>,
    yield_time_ms: u64,
}

/// Errors from routing or starting a workspace-bound command.
#[derive(Debug, Error)]
enum CommandOpError {
    #[error("layer_stack_root is required")]
    MissingLayerStackRoot,
    #[error(transparent)]
    LayerStack(#[from] eos_layerstack::LayerStackError),
    #[error(transparent)]
    Command(#[from] CommandSessionError),
}

/// `sandbox.command.exec` — command-session start contract.
pub(crate) fn op_exec_command(
    input: ExecCommandInput,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let command_config = command_session_config();
    let timeout_seconds = Some(exec_timeout_seconds(&input, &command_config));
    let yield_time_ms = input
        .yield_time_ms
        .unwrap_or(command_config.default_yield_time_ms);
    let response = exec_command(
        context.services().map(|services| &services.workspace),
        ExecCommandRequest {
            invocation_id: input.invocation_id.to_string(),
            caller_id: input.caller.to_string(),
            cmd: input.cmd,
            layer_stack_root: input.layer_stack_root,
            timeout_seconds,
            yield_time_ms,
        },
    )
    .map_err(command_op_error)?;
    let running = response.status == CommandStatus::Running;
    let wire = response.to_wire_value();
    if running {
        Ok(wire)
    } else {
        Ok(strip_session_id(wire))
    }
}

fn exec_timeout_seconds(
    input: &ExecCommandInput,
    config: &crate::config::CommandSessionConfig,
) -> f64 {
    u64_to_f64_saturating(input.timeout.unwrap_or(config.default_timeout_s))
}

fn exec_command(
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

pub(crate) fn op_command_collect_completed(
    input: CollectCompletedInput,
    _context: DispatchContext<'_>,
) -> Value {
    command_ops()
        .collect_completed(&collect_completed_request(input))
        .to_wire_value()
}

pub(crate) fn op_command_session_count(
    input: CallerCountInput,
    _context: DispatchContext<'_>,
) -> Value {
    let caller_id = input.caller.to_string();
    let count = command_ops().count_by_caller((!caller_id.is_empty()).then_some(&caller_id));
    to_wire_value(CommandSessionCountOutput {
        success: true,
        caller_id,
        count,
    })
}

pub(crate) fn command_session_write_stdin(
    input: WriteStdinInput,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let request = WriteStdin {
        command_session_id: input.command_session_id.to_string(),
        chars: input.chars,
        yield_time_ms: input
            .yield_time_ms
            .unwrap_or(command_session_config().default_yield_time_ms),
    };
    command_session_response_to_wire(command_ops().write_stdin(request))
}

pub(crate) fn command_session_read_progress(
    input: ReadProgressInput,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let request = ReadCommandProgress {
        command_session_id: input.command_session_id.to_string(),
        last_n_lines: input.last_n_lines,
    };
    command_session_response_to_wire(command_ops().read_command_progress(request))
}

pub(crate) fn command_session_cancel(
    input: CancelCommandInput,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let request = CancelCommandSession {
        command_session_id: input.command_session_id.to_string(),
    };
    command_session_response_to_wire(command_ops().cancel(request))
}

fn command_session_response_to_wire(
    response: Result<CommandResponse, CommandSessionError>,
) -> Result<Value, DaemonError> {
    match response {
        Ok(response) => Ok(response.to_wire_value()),
        // The not-found synthetic is not an error response; it stays a
        // CommandResponse-shaped output.
        Err(CommandSessionError::NotFound(_)) => {
            Ok(CommandResponse::error("command_session_not_found").to_wire_value())
        }
        Err(error) => Err(command_session_error(error)),
    }
}

fn strip_session_id(mut response: Value) -> Value {
    if let Some(object) = response.as_object_mut() {
        object.remove("command_session_id");
    }
    response
}

fn command_session_error(error: CommandSessionError) -> DaemonError {
    match error {
        CommandSessionError::Io(message) => DaemonError::OverlayPipeline(message),
        other => DaemonError::InvalidRequest(other.to_string()),
    }
}

fn command_op_error(error: CommandOpError) -> DaemonError {
    match error {
        CommandOpError::MissingLayerStackRoot => {
            DaemonError::InvalidRequest("layer_stack_root is required".to_owned())
        }
        CommandOpError::LayerStack(error) => DaemonError::LayerStack(error),
        CommandOpError::Command(error) => command_session_error(error),
    }
}

fn collect_completed_request(input: CollectCompletedInput) -> CollectCompleted {
    CollectCompleted {
        command_session_ids: input.command_session_ids.map(|ids| {
            ids.into_iter()
                .map(|command_session_id| command_session_id.to_string())
                .collect()
        }),
        caller_id: input.caller.map(|caller| caller.to_string()),
    }
}

#[cfg(test)]
#[path = "../../tests/unit/command/mod.rs"]
mod tests;
