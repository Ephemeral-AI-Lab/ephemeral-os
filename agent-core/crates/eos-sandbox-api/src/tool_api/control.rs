//! Pure daemon control RPCs: invocation cancel/heartbeat and the in-flight /
//! command-session / isolated-status counts. Ported from
//! `sandbox/api/daemon_invocations.py`. These do not carry caller identity and
//! use a fixed control timeout (`_CONTROL_TIMEOUT_S` in Python, lifted here).

use eos_types::{InvocationId, JsonObject, SandboxId};
use serde_json::Value;

use crate::error::SandboxApiError;
use crate::ops::DaemonOp;
use crate::transport::SandboxTransport;

/// Control-RPC timeout, seconds (Python `daemon_invocations._CONTROL_TIMEOUT_S`).
pub(crate) const CONTROL_TIMEOUT_S: u32 = 15;

/// Cancel an in-flight daemon invocation by id. Returns the raw daemon response.
pub async fn cancel(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    invocation_id: &InvocationId,
) -> Result<JsonObject, SandboxApiError> {
    let mut payload = JsonObject::new();
    payload.insert(
        "invocation_id".to_owned(),
        Value::String(invocation_id.to_string()),
    );
    transport
        .call(
            sandbox_id,
            DaemonOp::InvocationCancel,
            payload,
            CONTROL_TIMEOUT_S,
        )
        .await
}

/// Refresh liveness for a batch of in-flight daemon invocation ids.
pub async fn heartbeat(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    invocation_ids: &[InvocationId],
) -> Result<JsonObject, SandboxApiError> {
    let ids: Vec<Value> = invocation_ids
        .iter()
        .map(|id| Value::String(id.to_string()))
        .collect();
    let mut payload = JsonObject::new();
    payload.insert("invocation_ids".to_owned(), Value::Array(ids));
    transport
        .call(
            sandbox_id,
            DaemonOp::InvocationHeartbeat,
            payload,
            CONTROL_TIMEOUT_S,
        )
        .await
}

/// Daemon-visible in-flight invocation count for one agent (defaults to `0`).
pub async fn inflight_count(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    agent_id: &str,
) -> Result<u32, SandboxApiError> {
    let response =
        call_with_agent(transport, sandbox_id, DaemonOp::InflightCount, agent_id).await?;
    Ok(count_field(&response))
}

/// Daemon-visible live command-session count for one agent (defaults to `0`).
pub async fn command_session_count(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    agent_id: &str,
) -> Result<u32, SandboxApiError> {
    let response = call_with_agent(
        transport,
        sandbox_id,
        DaemonOp::CommandSessionCount,
        agent_id,
    )
    .await?;
    Ok(count_field(&response))
}

/// Whether the agent has an open isolated workspace (daemon truth). A response
/// without an `open` key (e.g. the no-pipeline error payload) is `false`.
pub async fn isolated_active(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    agent_id: &str,
) -> Result<bool, SandboxApiError> {
    let response = call_with_agent(
        transport,
        sandbox_id,
        DaemonOp::IsolatedWorkspaceStatus,
        agent_id,
    )
    .await?;
    Ok(response
        .get("open")
        .and_then(Value::as_bool)
        .unwrap_or(false))
}

async fn call_with_agent(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    op: DaemonOp,
    agent_id: &str,
) -> Result<JsonObject, SandboxApiError> {
    let mut payload = JsonObject::new();
    payload.insert("agent_id".to_owned(), Value::String(agent_id.to_owned()));
    transport
        .call(sandbox_id, op, payload, CONTROL_TIMEOUT_S)
        .await
}

/// `int(response.get("count") or 0)`.
fn count_field(response: &JsonObject) -> u32 {
    response
        .get("count")
        .and_then(Value::as_u64)
        .unwrap_or(0)
        .min(u64::from(u32::MAX)) as u32
}
