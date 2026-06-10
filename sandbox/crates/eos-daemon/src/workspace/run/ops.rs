//! Command-session dispatcher handlers, driving the caller-keyed
//! workspace-run manager ([`super::manager`]).

#[cfg(target_os = "linux")]
use std::path::PathBuf;

#[cfg(target_os = "linux")]
use eos_layerstack::require_workspace_binding;
#[cfg(target_os = "linux")]
use eos_command_session::{
    CancelCommandSession, CommandResponse, CommandSessionError, ReadCommandProgress,
    StartCommandSession, WriteStdin,
};
#[cfg(target_os = "linux")]
use eos_command_ops::ExecTarget;
use serde_json::{json, Value};

use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;

#[cfg(target_os = "linux")]
use super::manager::{command_session_config, command_session_scratch_root, command_ops};
#[cfg(not(target_os = "linux"))]
use super::wire::command_result;
#[cfg(any(target_os = "linux", test))]
use super::wire::optional_u64;
#[cfg(target_os = "linux")]
use super::wire::require_nonempty_string;
use super::wire::{caller_id_arg, command_session_not_found, require_command_string};
#[cfg(target_os = "linux")]
use super::wire::{
    collect_completed_request, command_response_to_wire, command_session_completion_to_wire,
    command_session_error, strip_session_id,
};

/// `api.v1.exec_command` — command-session start contract.
pub(crate) fn op_exec_command(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let cmd = require_command_string(args, "cmd")?;
    #[cfg(target_os = "linux")]
    {
        let command_config = command_session_config();
        let timeout_seconds = Some(exec_timeout_seconds(args, &command_config));
        let yield_time_ms =
            optional_u64(args, "yield_time_ms").unwrap_or(command_config.default_yield_time_ms);
        if let Some(binding) = crate::workspace::isolated::command_handle_for_args(args) {
            return start_manager_command_session(
                args,
                &cmd,
                timeout_seconds,
                yield_time_ms,
                binding.caller_id.clone(),
                ExecTarget::Isolated {
                    binding: Box::new(binding),
                },
            );
        }
        let root = PathBuf::from(require_command_string(args, "layer_stack_root")?);
        let binding = require_workspace_binding(&root)?;
        start_manager_command_session(
            args,
            &cmd,
            timeout_seconds,
            yield_time_ms,
            caller_id_arg(args).to_owned(),
            ExecTarget::Ephemeral {
                root,
                workspace_root: PathBuf::from(binding.workspace_root),
                scratch_root: command_session_scratch_root(),
            },
        )
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = &cmd;
        if crate::workspace::isolated::caller_has_active_handle(caller_id_arg(args)) {
            return Ok(command_result(
                "error",
                None,
                "",
                "isolated exec_command is only supported on linux",
                None,
            ));
        }
        Ok(command_result(
            "error",
            None,
            "",
            "command sessions are only supported on linux",
            None,
        ))
    }
}

#[cfg(any(target_os = "linux", test))]
fn u64_to_f64_saturating(value: u64) -> f64 {
    u32::try_from(value).map_or_else(|_| f64::from(u32::MAX), f64::from)
}

#[cfg(any(target_os = "linux", test))]
fn exec_timeout_seconds(args: &Value, config: &crate::config::CommandSessionConfig) -> f64 {
    u64_to_f64_saturating(
        optional_u64(args, "timeout")
            .or_else(|| optional_u64(args, "timeout_seconds"))
            .unwrap_or(config.default_timeout_s),
    )
}

// Dispatcher op handlers share the `Result<Value, DaemonError>` ABI even when
// a specific op encodes all domain failures in its JSON response.
#[cfg_attr(
    not(target_os = "linux"),
    expect(
        clippy::unnecessary_wraps,
        reason = "dispatcher handlers share a fallible ABI"
    )
)]
pub(crate) fn op_command_write_stdin(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    #[cfg(target_os = "linux")]
    {
        command_session_write_stdin(args)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = args;
        Ok(command_session_not_found())
    }
}

#[cfg_attr(
    not(target_os = "linux"),
    expect(
        clippy::unnecessary_wraps,
        reason = "dispatcher handlers share a fallible ABI"
    )
)]
pub(crate) fn op_command_read_progress(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    #[cfg(target_os = "linux")]
    {
        command_session_read_progress(args)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = args;
        Ok(command_session_not_found())
    }
}

#[cfg_attr(
    not(target_os = "linux"),
    expect(
        clippy::unnecessary_wraps,
        reason = "dispatcher handlers share a fallible ABI"
    )
)]
pub(crate) fn op_command_cancel(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    #[cfg(target_os = "linux")]
    {
        command_session_cancel(args)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = args;
        Ok(command_session_not_found())
    }
}

#[expect(
    clippy::unnecessary_wraps,
    reason = "dispatcher handlers share a fallible ABI"
)]
pub(crate) fn op_command_collect_completed(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    #[cfg(target_os = "linux")]
    {
        let response = command_ops().collect_completed(&collect_completed_request(args));
        let completions = response
            .completions
            .into_iter()
            .map(command_session_completion_to_wire)
            .collect::<Vec<_>>();
        Ok(json!({"success": response.success, "completions": completions}))
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = args;
        Ok(json!({"success": true, "completions": []}))
    }
}

#[expect(
    clippy::unnecessary_wraps,
    reason = "dispatcher handlers share a fallible ABI"
)]
pub(crate) fn op_command_session_count(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let caller_id = args
        .get("caller_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_owned();
    #[cfg(target_os = "linux")]
    {
        let count =
            command_ops().count_by_caller((!caller_id.is_empty()).then_some(&caller_id));
        Ok(json!({"success": true, "caller_id": caller_id, "count": count}))
    }
    #[cfg(not(target_os = "linux"))]
    {
        Ok(json!({"success": true, "caller_id": caller_id, "count": 0}))
    }
}

#[cfg(target_os = "linux")]
fn start_manager_command_session(
    args: &Value,
    cmd: &str,
    timeout_seconds: Option<f64>,
    yield_time_ms: u64,
    caller_id: String,
    target: ExecTarget,
) -> Result<Value, DaemonError> {
    let request = StartCommandSession {
        invocation_id: args
            .get("invocation_id")
            .and_then(Value::as_str)
            .unwrap_or("exec_command")
            .to_owned(),
        caller_id,
        cmd: cmd.to_owned(),
        timeout_seconds,
        yield_time_ms,
    };
    let response = command_ops()
        .exec_command(request, target)
        .map_err(command_session_error)?;
    let wire = command_response_to_wire(response);
    if wire
        .get("status")
        .and_then(Value::as_str)
        .is_some_and(|status| status == "running")
    {
        Ok(wire)
    } else {
        Ok(strip_session_id(wire))
    }
}

#[cfg(target_os = "linux")]
fn command_session_write_stdin(args: &Value) -> Result<Value, DaemonError> {
    let request = WriteStdin {
        command_session_id: require_command_string(args, "command_session_id")?,
        chars: require_nonempty_string(args, "chars")?,
        yield_time_ms: optional_u64(args, "yield_time_ms")
            .unwrap_or(command_session_config().default_yield_time_ms),
    };
    command_session_response_to_wire(command_ops().write_stdin(request))
}

#[cfg(target_os = "linux")]
fn command_session_read_progress(args: &Value) -> Result<Value, DaemonError> {
    let last_n_lines = optional_u64(args, "last_n_lines").unwrap_or(50);
    let request = ReadCommandProgress {
        command_session_id: require_command_string(args, "command_session_id")?,
        last_n_lines: last_n_lines
            .try_into()
            .map_err(|_| DaemonError::InvalidEnvelope("last_n_lines is too large".to_owned()))?,
    };
    command_session_response_to_wire(command_ops().read_command_progress(request))
}

#[cfg(target_os = "linux")]
fn command_session_cancel(args: &Value) -> Result<Value, DaemonError> {
    let request = CancelCommandSession {
        command_session_id: require_command_string(args, "command_session_id")?,
    };
    command_session_response_to_wire(command_ops().cancel(request))
}

#[cfg(target_os = "linux")]
fn command_session_response_to_wire(
    response: Result<CommandResponse, CommandSessionError>,
) -> Result<Value, DaemonError> {
    match response {
        Ok(response) => Ok(command_response_to_wire(response)),
        Err(CommandSessionError::NotFound(_)) => Ok(command_session_not_found()),
        Err(error) => Err(command_session_error(error)),
    }
}

#[cfg(test)]
#[path = "../../../tests/unit/command/mod.rs"]
mod tests;
