//! Command dispatcher handlers, driving the caller-keyed
//! command runtime in `eos_operation::command`.

use std::path::PathBuf;

use eos_command::{
    CancelCommand, CollectCompleted, CommandError, ReadCommandProgress, StartCommand, WriteStdin,
};
use eos_operation::command::contract::{
    CancelCommandInput, CollectCompletedInput, CommandCountOutput, CommandResponse, CommandStatus,
    ExecCommandInput, ReadProgressInput, WriteStdinInput,
};
use eos_operation::command::{command_config, command_ops, command_scratch_root, ExecTarget};
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
    trace_id: Option<String>,
    request_id: Option<String>,
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
    Command(#[from] CommandError),
}

/// `sandbox.command.exec` - command start contract.
pub(crate) fn op_exec_command(
    input: ExecCommandInput,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let command_config = command_config();
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
            trace_id: context.trace_id().map(str::to_owned),
            request_id: context.request_id().map(str::to_owned),
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
        Ok(strip_command_id(wire))
    }
}

fn exec_timeout_seconds(input: &ExecCommandInput, config: &crate::config::CommandConfig) -> f64 {
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
        trace_id,
        request_id,
        layer_stack_root,
        timeout_seconds,
        yield_time_ms,
    } = request;

    if let Some(binding) = workspace.and_then(|workspace| workspace.command_binding_for(&caller_id))
    {
        return command_ops()
            .exec_command(
                StartCommand {
                    invocation_id,
                    caller_id: binding.caller_id.clone(),
                    cmd,
                    trace_id,
                    request_id,
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
            StartCommand {
                invocation_id,
                caller_id,
                cmd,
                trace_id,
                request_id,
                timeout_seconds,
                yield_time_ms,
            },
            ExecTarget::Ephemeral {
                root,
                workspace_root: PathBuf::from(binding.workspace_root),
                scratch_root: command_scratch_root(),
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

pub(crate) fn op_command_count(input: CallerCountInput, _context: DispatchContext<'_>) -> Value {
    let caller_id = input.caller.to_string();
    let count = command_ops().count_by_caller((!caller_id.is_empty()).then_some(&caller_id));
    to_wire_value(CommandCountOutput {
        success: true,
        caller_id,
        count,
    })
}

pub(crate) fn command_write_stdin(
    input: WriteStdinInput,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let request = WriteStdin {
        command_id: input.command_id.to_string(),
        chars: input.chars,
        yield_time_ms: input
            .yield_time_ms
            .unwrap_or(command_config().default_yield_time_ms),
    };
    command_response_to_wire(command_ops().write_stdin(request))
}

pub(crate) fn command_read_progress(
    input: ReadProgressInput,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let request = ReadCommandProgress {
        command_id: input.command_id.to_string(),
        last_n_lines: input.last_n_lines,
    };
    command_response_to_wire(command_ops().read_command_progress(request))
}

pub(crate) fn command_cancel(
    input: CancelCommandInput,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let request = CancelCommand {
        command_id: input.command_id.to_string(),
    };
    command_response_to_wire(command_ops().cancel(request))
}

fn command_response_to_wire(
    response: Result<CommandResponse, CommandError>,
) -> Result<Value, DaemonError> {
    match response {
        Ok(response) => Ok(response.to_wire_value()),
        // The not-found synthetic is not an error response; it stays a
        // CommandResponse-shaped output.
        Err(CommandError::NotFound(_)) => {
            Ok(CommandResponse::error("command_not_found").to_wire_value())
        }
        Err(error) => Err(command_error(error)),
    }
}

fn strip_command_id(mut response: Value) -> Value {
    if let Some(object) = response.as_object_mut() {
        object.remove("command_id");
    }
    response
}

fn command_error(error: CommandError) -> DaemonError {
    match error {
        CommandError::Io(message) => DaemonError::OverlayPipeline(message),
        other => DaemonError::InvalidRequest(other.to_string()),
    }
}

fn command_op_error(error: CommandOpError) -> DaemonError {
    match error {
        CommandOpError::MissingLayerStackRoot => {
            DaemonError::InvalidRequest("layer_stack_root is required".to_owned())
        }
        CommandOpError::LayerStack(error) => DaemonError::LayerStack(error),
        CommandOpError::Command(error) => command_error(error),
    }
}

fn collect_completed_request(input: CollectCompletedInput) -> CollectCompleted {
    CollectCompleted {
        command_ids: input.command_ids.map(|ids| {
            ids.into_iter()
                .map(|command_id| command_id.to_string())
                .collect()
        }),
        caller_id: input.caller.map(|caller| caller.to_string()),
    }
}

#[cfg(test)]
#[path = "../../tests/unit/command/mod.rs"]
mod tests;
