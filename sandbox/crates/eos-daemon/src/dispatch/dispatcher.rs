//! Op routing and request validation. Built-ins are mapped from the catalog;
//! `plugin.*` misses defer to the runtime plugin registry.

#[cfg(test)]
use std::path::PathBuf;
use std::time::Instant;

use eos_operation::core::catalog::BuiltinOp;
use eos_operation::{
    ArgsError, OpError, OpRequest, OpResponse, OpResponseError, OpResponseErrorKind, RequestError,
};
use serde_json::{json, Value};

use crate::wire::{ErrorKind, Request};
#[cfg(test)]
use eos_layerstack::LayerStack;

use crate::error::DaemonError;
#[cfg(test)]
use crate::invocation_registry::InFlightRegistry;
use crate::op_adapter::{checkpoint, command, control, files, isolation, plugin, workspace_run};
#[cfg(test)]
use crate::response::{insert_tree_resource_timings, resource_timings, TreeResourceStats};
use crate::DispatchContext;

#[must_use]
pub fn dispatch(request: &Request) -> Value {
    dispatch_with_context(request, DispatchContext::empty())
}

#[must_use]
pub fn dispatch_with_context(request: &Request, context: DispatchContext<'_>) -> Value {
    let dispatch_start = Instant::now();
    let boot_to_dispatch_s = daemon_uptime_s();
    let read_request_s = context.read_request_s().unwrap_or(0.0);
    let finalize =
        |response| finalize_response(response, boot_to_dispatch_s, dispatch_start, read_request_s);
    if request.op.trim().is_empty() {
        return finalize(error_response(
            ErrorKind::InvalidRequest,
            "op is required",
            json!({}),
        ));
    }
    if !request.args.is_object() {
        return finalize(error_response(
            ErrorKind::InvalidRequest,
            "args must be an object",
            json!({}),
        ));
    }
    let Some(op) = BuiltinOp::from_op_name(&request.op) else {
        return finalize(plugin_fallback_or_unknown(request, context));
    };
    let parsed = match OpRequest::parse(op, &request.args) {
        Ok(parsed) => parsed,
        Err(RequestError::Args(error)) => {
            return finalize(parse_error_response(op, error).into_wire())
        }
        Err(RequestError::NotDaemonServed(_)) => {
            return finalize(plugin_fallback_or_unknown(request, context));
        }
    };
    finalize(dispatch_builtin(parsed, context).into_wire())
}

fn plugin_fallback_or_unknown(request: &Request, context: DispatchContext<'_>) -> Value {
    if let Some(response) =
        plugin::dispatch_registered_op(&request.op, &request.invocation_id, &request.args, context)
    {
        return match response {
            Ok(response) => response,
            Err(err) => error_response(err.wire_kind(), &err.to_string(), json!({})),
        };
    }
    error_response(
        ErrorKind::UnknownOp,
        &format!("unknown op: {}", request.op),
        json!({"op": request.op}),
    )
}

fn dispatch_builtin(request: OpRequest, context: DispatchContext<'_>) -> OpResponse {
    match request {
        OpRequest::RuntimeReady(input) => daemon_result(control::op_runtime_ready(input, context)),
        OpRequest::InvocationHeartbeat(input) => {
            OpResponse::Success(control::op_heartbeat(input, context))
        }
        OpRequest::InvocationCancel(input) => {
            OpResponse::Success(control::op_cancel(input, context))
        }
        OpRequest::InflightCount(input) => {
            OpResponse::Success(control::op_inflight_count(input, context))
        }
        OpRequest::LayerMetrics(input) => daemon_result(checkpoint::layer_metrics(input, context)),
        OpRequest::EnsureWorkspaceBase(input) => {
            daemon_result(checkpoint::ensure_workspace_base(input, context))
        }
        OpRequest::BuildWorkspaceBase(input) => {
            daemon_result(checkpoint::build_workspace_base(input, context))
        }
        OpRequest::CommitToWorkspace(input) => {
            daemon_result(checkpoint::commit_to_workspace(input, context))
        }
        OpRequest::CommitToGit(input) => daemon_result(checkpoint::commit_to_git(input, context)),
        OpRequest::WorkspaceBinding(input) => {
            daemon_result(checkpoint::workspace_binding(input, context))
        }
        OpRequest::ReadFile(input) => daemon_result(files::op_read_file(input, context)),
        OpRequest::WriteFile(input) => daemon_result(files::op_write_file(input, context)),
        OpRequest::EditFile(input) => daemon_result(files::op_edit_file(input, context)),
        OpRequest::PluginEnsure(input) => daemon_result(plugin::op_ensure(*input, context)),
        OpRequest::PluginStatus(input) => daemon_result(plugin::op_status(input, context)),
        OpRequest::IsolatedWorkspaceEnter(input) => {
            daemon_response_result(isolation::op_enter(input, context))
        }
        OpRequest::IsolatedWorkspaceExit(input) => {
            daemon_response_result(isolation::op_exit(input, context))
        }
        OpRequest::IsolatedWorkspaceStatus(input) => {
            daemon_response_result(isolation::op_status(input, context))
        }
        OpRequest::IsolatedWorkspaceListOpen => {
            daemon_response_result(isolation::op_list_open(context))
        }
        OpRequest::IsolatedWorkspaceTestReset => {
            daemon_response_result(isolation::op_test_reset(context))
        }
        OpRequest::ExecCommand(input) => daemon_result(command::op_exec_command(input, context)),
        OpRequest::WriteStdin(input) => {
            daemon_result(command::command_session_write_stdin(input, context))
        }
        OpRequest::CommandReadProgress(input) => {
            daemon_result(command::command_session_read_progress(input, context))
        }
        OpRequest::CommandCancel(input) => {
            daemon_result(command::command_session_cancel(input, context))
        }
        OpRequest::CommandCollectCompleted(input) => {
            OpResponse::Success(command::op_command_collect_completed(input, context))
        }
        OpRequest::CommandSessionCount(input) => {
            OpResponse::Success(command::op_command_session_count(input, context))
        }
        OpRequest::CancelWorkspaceRunsByCaller(input) => daemon_result(
            workspace_run::op_cancel_workspace_runs_by_caller_id(input, context),
        ),
        OpRequest::CancelWorkspaceRuns(input) => {
            daemon_result(workspace_run::op_cancel_workspace_runs(input, context))
        }
    }
}

fn daemon_result(result: Result<Value, DaemonError>) -> OpResponse {
    match result {
        Ok(value) => OpResponse::Success(value),
        Err(err) => daemon_error(err),
    }
}

fn daemon_response_result(result: Result<OpResponse, DaemonError>) -> OpResponse {
    match result {
        Ok(response) => response,
        Err(err) => daemon_error(err),
    }
}

fn daemon_error(err: DaemonError) -> OpResponse {
    OpResponse::Error(OpResponseError::new(
        response_error_kind(err.wire_kind()),
        err.to_string(),
        json!({}),
    ))
}

fn response_error_kind(kind: ErrorKind) -> OpResponseErrorKind {
    match kind {
        ErrorKind::InvalidRequest => OpResponseErrorKind::InvalidRequest,
        ErrorKind::BadJson => OpResponseErrorKind::BadJson,
        ErrorKind::RequestTooLarge => OpResponseErrorKind::RequestTooLarge,
        ErrorKind::Unauthorized => OpResponseErrorKind::Unauthorized,
        ErrorKind::UnknownOp => OpResponseErrorKind::UnknownOp,
        ErrorKind::InternalError => OpResponseErrorKind::InternalError,
        ErrorKind::Forbidden => OpResponseErrorKind::Forbidden,
        ErrorKind::ForbiddenInIsolatedWorkspace => {
            OpResponseErrorKind::ForbiddenInIsolatedWorkspace
        }
        ErrorKind::LifecycleInProgress => OpResponseErrorKind::LifecycleInProgress,
    }
}

fn parse_error_response(op: BuiltinOp, error: ArgsError) -> OpResponse {
    match op.contract().family {
        eos_operation::core::catalog::OpFamily::IsolatedWorkspace
        | eos_operation::core::catalog::OpFamily::WorkspaceRun => OpResponse::Refused(OpError {
            kind: "invalid_argument",
            message: error.message(),
            details: Some(json!({"key": error.key})),
        }),
        _ => OpResponse::Error(OpResponseError::invalid_request(format!(
            "invalid request: {}",
            error.message()
        ))),
    }
}

fn finalize_response(
    mut response: Value,
    boot_to_dispatch_s: f64,
    dispatch_start: Instant,
    read_request_s: f64,
) -> Value {
    let dispatch_s = dispatch_start.elapsed().as_secs_f64();
    attach_runtime_timings(
        &mut response,
        boot_to_dispatch_s,
        dispatch_s,
        read_request_s,
    );
    response
}

#[must_use]
pub(crate) fn error_response(kind: ErrorKind, message: &str, details: Value) -> Value {
    let is_internal_error = kind == ErrorKind::InternalError;
    let kind_str = serde_json::to_value(kind).unwrap_or(Value::Null);
    let details = error_details(is_internal_error, details);
    json!({
        "success": false,
        "warnings": [],
        "timings": {},
        "error": {
            "kind": kind_str,
            "message": message,
            "details": details,
        },
    })
}

fn error_details(is_internal_error: bool, details: Value) -> Value {
    if !is_internal_error {
        return if details.is_null() {
            json!({})
        } else {
            details
        };
    }
    let mut details = match details {
        Value::Null => serde_json::Map::new(),
        Value::Object(details) => details,
        other => {
            let mut object = serde_json::Map::new();
            object.insert("value".to_owned(), other);
            object
        }
    };
    details
        .entry("error_id")
        .or_insert_with(|| Value::String(new_error_id()));
    Value::Object(details)
}

fn new_error_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

fn attach_runtime_timings(
    response: &mut Value,
    boot_to_dispatch_s: f64,
    dispatch_s: f64,
    read_request_s: f64,
) {
    let Some(obj) = response.as_object_mut() else {
        return;
    };
    let timings = obj
        .entry("timings")
        .or_insert_with(|| Value::Object(serde_json::Map::new()));
    if let Value::Object(timings) = timings {
        timings.insert(
            "runtime.boot_to_dispatch_s".to_owned(),
            json!(boot_to_dispatch_s),
        );
        timings.insert("runtime.dispatch_s".to_owned(), json!(dispatch_s));
        timings.insert("runtime.read_request_s".to_owned(), json!(read_request_s));
    }
}

pub(crate) fn daemon_uptime_s() -> f64 {
    static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}

#[cfg(test)]
#[path = "../../tests/unit/dispatcher/mod.rs"]
mod tests;
