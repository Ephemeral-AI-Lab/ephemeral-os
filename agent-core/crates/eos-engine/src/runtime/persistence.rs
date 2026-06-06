//! Agent-run persistence helpers for the engine runtime.

use eos_types::{AgentRunId, TaskId};

use super::types::EngineRunHandles;

pub(super) async fn create_agent_run_if_requested(
    handles: &EngineRunHandles,
    persist_agent_run: bool,
    task_id: Option<&TaskId>,
    agent_run_id: &AgentRunId,
    agent_name: &str,
) -> bool {
    let Some(tid) = task_id.filter(|_| persist_agent_run) else {
        return false;
    };
    if let Err(err) = handles
        .agent_run_store
        .create_run(agent_run_id, tid, agent_name, None)
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
    error: Option<&str>,
) {
    if !persistence_requested {
        return;
    }
    if let Err(err) = handles
        .agent_run_store
        .finish_run(agent_run_id, None, None, 0, error)
        .await
    {
        tracing::warn!(error = %err, "agent_run finish_run failed (non-fatal)");
    }
}
