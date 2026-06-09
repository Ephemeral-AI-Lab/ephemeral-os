//! Cancel user-request orchestration.

use eos_agent_run::AgentRunService;
use eos_types::{AgentRunApi, RequestStatus};

use crate::dto::{CancelUserRequestInput, CancelUserRequestOutput};
use crate::error::AgentCoreServerError;
use crate::request_state::RequestState;

pub(crate) async fn cancel_user_request(
    state: &RequestState,
    agent_run_service: &AgentRunService,
    input: CancelUserRequestInput,
) -> Result<CancelUserRequestOutput, AgentCoreServerError> {
    let Some(request) = state.request_store.get(&input.request_id).await? else {
        return Err(AgentCoreServerError::UserRequestNotFound(input.request_id));
    };
    if request.status.is_terminal() {
        return Err(AgentCoreServerError::UserRequestAlreadyFinished {
            request_id: request.id,
            status: request.status,
        });
    }

    let running = state
        .task_agent_run_store
        .list_running_agent_runs_for_request(&input.request_id)
        .await?;
    for run in &running {
        agent_run_service
            .cancel_agent_run(&run.agent_run_id, &input.reason)
            .await?;
    }
    state
        .workflow_store
        .cancel_open_workflows_for_request(&input.request_id, &input.reason)
        .await?;
    state
        .iteration_store
        .cancel_open_iterations_for_request(&input.request_id, &input.reason)
        .await?;
    state
        .attempt_store
        .cancel_open_attempts_for_request(&input.request_id, &input.reason)
        .await?;
    state
        .request_store
        .finish_request(&input.request_id, RequestStatus::Cancelled)
        .await?;

    Ok(CancelUserRequestOutput {
        request_id: input.request_id,
        cancelled_agent_run_count: running.len(),
    })
}
