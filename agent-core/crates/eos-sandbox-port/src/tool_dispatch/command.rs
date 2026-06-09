//! Pure command-session helpers: `exec_command`, `exec_stdin`,
//! `cancel_command_session`, and `collect_command_completions`. The first three
//! return [`ExecCommandResult`]; `collect_command_completions` returns the raw
//! completion maps (the only verb without a typed result struct).

use eos_types::{JsonObject, SandboxId};
use serde_json::Value;

use crate::error::SandboxPortError;
use crate::models::{
    CommandSessionCancelRequest, ExecCommandRequest, ExecCommandResult, ExecStdinRequest,
    ReadCommandProgressRequest,
};
use crate::ops::DaemonOp;
use crate::timeouts::exec_dispatch_timeout;
use crate::tool_dispatch::parse::{daemon_request_identity_fields, parse_exec_command_result};
use crate::transport::SandboxTransport;

/// Run or start a managed command session.
pub async fn exec_command(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    request: &ExecCommandRequest,
) -> Result<ExecCommandResult, SandboxPortError> {
    let mut payload = daemon_request_identity_fields(&request.base);
    payload.insert("cmd".to_owned(), Value::String(request.cmd.clone()));
    if let Some(yield_time_ms) = request.yield_time_ms {
        payload.insert("yield_time_ms".to_owned(), Value::from(yield_time_ms));
    }
    if let Some(timeout) = request.timeout {
        payload.insert("timeout".to_owned(), Value::from(timeout));
    }
    let response = transport
        .call(
            sandbox_id,
            DaemonOp::ExecCommand,
            payload,
            exec_dispatch_timeout(request.timeout),
        )
        .await?;
    parse_exec_command_result(&response)
}

/// Write characters (stdin) to an open command session.
pub async fn exec_stdin(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    request: &ExecStdinRequest,
) -> Result<ExecCommandResult, SandboxPortError> {
    let mut payload = daemon_request_identity_fields(&request.base);
    payload.insert(
        "command_session_id".to_owned(),
        Value::String(request.command_session_id.to_string()),
    );
    payload.insert("chars".to_owned(), Value::String(request.chars.clone()));
    if let Some(yield_time_ms) = request.yield_time_ms {
        payload.insert("yield_time_ms".to_owned(), Value::from(yield_time_ms));
    }
    let response = transport
        .call(
            sandbox_id,
            DaemonOp::ExecStdin,
            payload,
            exec_dispatch_timeout(None),
        )
        .await?;
    parse_exec_command_result(&response)
}

/// Read a stateless tail snapshot from an open command session.
pub async fn read_command_progress(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    request: &ReadCommandProgressRequest,
) -> Result<ExecCommandResult, SandboxPortError> {
    let mut payload = daemon_request_identity_fields(&request.base);
    payload.insert(
        "command_session_id".to_owned(),
        Value::String(request.command_session_id.to_string()),
    );
    payload.insert("last_n_lines".to_owned(), Value::from(request.last_n_lines));
    let response = transport
        .call(
            sandbox_id,
            DaemonOp::CommandReadProgress,
            payload,
            exec_dispatch_timeout(None),
        )
        .await?;
    parse_exec_command_result(&response)
}

/// Cancel an open command session.
pub async fn cancel_command_session(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    request: &CommandSessionCancelRequest,
) -> Result<ExecCommandResult, SandboxPortError> {
    let mut payload = daemon_request_identity_fields(&request.base);
    payload.insert(
        "command_session_id".to_owned(),
        Value::String(request.command_session_id.to_string()),
    );
    let response = transport
        .call(
            sandbox_id,
            DaemonOp::CommandCancel,
            payload,
            exec_dispatch_timeout(None),
        )
        .await?;
    parse_exec_command_result(&response)
}

/// Collect completed background command sessions for one caller. Returns the raw
/// completion maps (objects only; non-object entries are dropped).
pub async fn collect_command_completions(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    caller_id: &str,
    command_session_ids: &[String],
) -> Result<Vec<JsonObject>, SandboxPortError> {
    let mut payload = JsonObject::new();
    payload.insert("caller_id".to_owned(), Value::String(caller_id.to_owned()));
    payload.insert(
        "command_session_ids".to_owned(),
        Value::Array(
            command_session_ids
                .iter()
                .map(|id| Value::String(id.clone()))
                .collect(),
        ),
    );
    let response = transport
        .call(
            sandbox_id,
            DaemonOp::CommandCollectCompleted,
            payload,
            exec_dispatch_timeout(None),
        )
        .await?;
    let completions = match response.get("completions") {
        Some(Value::Array(items)) => items
            .iter()
            .filter_map(|item| item.as_object().cloned())
            .collect(),
        _ => Vec::new(),
    };
    Ok(completions)
}

/// Cancel every workspace run owned by one caller in one RPC
/// (`caller_id == agent_run_id`): the daemon discards the caller's command
/// session(s) and exits its isolated workspace if open. This is agent-core's
/// per-run command-session teardown — one call replaces per-session cancels.
/// Returns the daemon's raw response object (`cancelled_command_sessions`,
/// `isolated_exited`); the caller may ignore it on success.
pub async fn cancel_workspace_runs_by_caller_id(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    caller_id: &str,
) -> Result<JsonObject, SandboxPortError> {
    let mut payload = JsonObject::new();
    payload.insert("caller_id".to_owned(), Value::String(caller_id.to_owned()));
    transport
        .call(
            sandbox_id,
            DaemonOp::CancelWorkspaceRunsByCaller,
            payload,
            exec_dispatch_timeout(None),
        )
        .await
}
