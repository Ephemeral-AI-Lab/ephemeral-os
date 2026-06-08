//! Agent-run persistence helpers for the engine runtime.

use eos_tools::ToolResult;
use eos_types::{AgentRunId, JsonObject, TaskId};
use serde_json::json;

use super::types::EngineRunHandles;

pub(super) async fn create_agent_run_if_requested(
    handles: &EngineRunHandles,
    persist_agent_run: bool,
    task_id: Option<&TaskId>,
    agent_run_id: &AgentRunId,
    agent_name: &str,
) -> bool {
    if !persist_agent_run {
        return false;
    }
    if let Err(err) = handles
        .agent_run_store
        .create_run(agent_run_id, task_id, agent_name, None)
        .await
    {
        tracing::warn!(error = %err, "agent_run create_run failed (non-fatal)");
    }
    true
}

pub(super) async fn finish_agent_run_if_requested(
    handles: &EngineRunHandles,
    persistence_requested: bool,
    agent_run_id: &AgentRunId,
    submission_outcome: Option<&ToolResult>,
    error: Option<&str>,
) {
    if !persistence_requested {
        return;
    }
    let submission_payload = submission_outcome.map(tool_result_payload);
    if let Err(err) = handles
        .agent_run_store
        .finish_run(agent_run_id, None, submission_payload.as_ref(), 0, error)
        .await
    {
        tracing::warn!(error = %err, "agent_run finish_run failed (non-fatal)");
    }
}

fn tool_result_payload(result: &ToolResult) -> JsonObject {
    let mut payload = JsonObject::new();
    payload.insert("output".to_owned(), json!(result.output));
    payload.insert("is_error".to_owned(), json!(result.is_error));
    payload.insert("metadata".to_owned(), json!(result.metadata));
    payload.insert("is_terminal".to_owned(), json!(result.is_terminal));
    payload
}
