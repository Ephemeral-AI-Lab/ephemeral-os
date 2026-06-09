//! Agent-run persistence helpers owned by the runner.

use eos_types::{
    AgentRun, AgentRunId, AgentRunOutcome, AgentRunStatus, AgentRunStore, JsonObject,
    SubmissionOutcome, ExecutionStatus,
};

use crate::AgentRunError;

pub(crate) async fn finish_agent_run(
    store: &dyn AgentRunStore,
    agent_run_id: &AgentRunId,
    status: ExecutionStatus,
    terminal_payload: Option<&JsonObject>,
    token_count: i64,
    error: Option<&str>,
) -> Result<(), AgentRunError> {
    let submission_outcome = terminal_payload.and_then(decode_submission_outcome);
    if store
        .finish_agent_run(
            agent_run_id,
            status,
            terminal_payload,
            submission_outcome.as_ref(),
            token_count,
            error,
        )
        .await
        .map_err(|err| AgentRunError::Internal(err.to_string()))?
        .is_some()
    {
        return Ok(());
    }

    Err(AgentRunError::Internal(format!(
        "agent-run row not updated for {}",
        agent_run_id.as_str()
    )))
}

pub(crate) fn completion_from_agent_run(
    agent_run_id: &AgentRunId,
    run: &AgentRun,
) -> Option<AgentRunOutcome> {
    completion_from_parts(
        agent_run_id,
        run.finished_at,
        run.terminal_payload.as_ref(),
        run.token_count,
        run.error.as_ref(),
    )
}

fn completion_from_parts(
    agent_run_id: &AgentRunId,
    finished_at: Option<eos_types::UtcDateTime>,
    terminal_payload: Option<&JsonObject>,
    token_count: i64,
    error: Option<&String>,
) -> Option<AgentRunOutcome> {
    finished_at?;
    if let Some(terminal) = terminal_payload {
        if is_cancelled_payload(terminal) {
            return Some(AgentRunOutcome {
                agent_run_id: agent_run_id.clone(),
                status: AgentRunStatus::Cancelled,
                submission_payload: None,
                message_history: Vec::new(),
                token_count: Some(token_count),
                error: error.cloned(),
            });
        }
        return Some(AgentRunOutcome {
            agent_run_id: agent_run_id.clone(),
            status: AgentRunStatus::Completed,
            submission_payload: Some(terminal.clone()),
            message_history: Vec::new(),
            token_count: Some(token_count),
            error: error.cloned(),
        });
    }
    let message = match error {
        Some(error) => format!("agent run failed: {error}"),
        None => "agent run failed without terminal outcome".to_owned(),
    };
    Some(AgentRunOutcome {
        agent_run_id: agent_run_id.clone(),
        status: AgentRunStatus::Failed,
        submission_payload: None,
        message_history: Vec::new(),
        token_count: Some(token_count),
        error: Some(message),
    })
}

fn is_cancelled_payload(payload: &JsonObject) -> bool {
    payload
        .get("fail_reason")
        .and_then(serde_json::Value::as_str)
        == Some("cancelled")
}

fn decode_submission_outcome(payload: &JsonObject) -> Option<SubmissionOutcome> {
    serde_json::from_value(serde_json::Value::Object(payload.clone())).ok()
}
