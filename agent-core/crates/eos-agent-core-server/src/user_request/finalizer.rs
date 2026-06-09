//! Private request finalizer for root-agent outcomes.

use std::sync::Arc;

use eos_agent_run::AgentRunService;
use eos_types::{AgentRunApi, AgentRunId, AgentRunStatus, RequestId, RequestStatus, RequestStore};

pub(crate) async fn finish_user_request_after_root_agent(
    request_store: Arc<dyn RequestStore>,
    agent_run_service: AgentRunService,
    request_id: RequestId,
    root_agent_run_id: AgentRunId,
) {
    let request_status = match agent_run_service
        .wait_for_agent_outcome(&root_agent_run_id)
        .await
    {
        Ok(outcome) => request_status_for_agent_status(outcome.status),
        Err(err) => {
            tracing::warn!(
                request_id = request_id.as_str(),
                agent_run_id = root_agent_run_id.as_str(),
                error = %err,
                "root agent outcome wait failed; marking request failed"
            );
            RequestStatus::Failed
        }
    };
    if let Err(err) = request_store
        .finish_request(&request_id, request_status)
        .await
    {
        tracing::warn!(
            request_id = request_id.as_str(),
            agent_run_id = root_agent_run_id.as_str(),
            error = %err,
            "request finalizer failed to write terminal status"
        );
    }
}

const fn request_status_for_agent_status(status: AgentRunStatus) -> RequestStatus {
    match status {
        AgentRunStatus::Completed => RequestStatus::Done,
        AgentRunStatus::Failed => RequestStatus::Failed,
        AgentRunStatus::Cancelled => RequestStatus::Cancelled,
    }
}
