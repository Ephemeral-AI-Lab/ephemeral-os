//! Cancel user-request orchestration.

use eos_types::{AgentRunApi, RequestStatus};

use crate::dto::{CancelUserRequestInput, CancelUserRequestOutput};
use crate::error::AgentCoreServerError;
use crate::service::AgentCoreService;

pub(crate) async fn cancel_user_request(
    service: &AgentCoreService,
    input: CancelUserRequestInput,
) -> Result<CancelUserRequestOutput, AgentCoreServerError> {
    let Some(request) = service.request_store.get(&input.request_id).await? else {
        return Err(AgentCoreServerError::UserRequestNotFound(input.request_id));
    };
    if request.status.is_terminal() {
        return Err(AgentCoreServerError::UserRequestAlreadyFinished {
            request_id: request.id,
            status: request.status,
        });
    }

    let running = service
        .task_agent_run_store
        .list_running_agent_runs_for_request(&input.request_id)
        .await?;
    for run in &running {
        service
            .agent_run_service
            .cancel_agent_run(&run.agent_run_id, &input.reason)
            .await?;
    }
    service
        .workflow_store
        .cancel_open_workflows_for_request(&input.request_id, &input.reason)
        .await?;
    service
        .iteration_store
        .cancel_open_iterations_for_request(&input.request_id, &input.reason)
        .await?;
    service
        .attempt_store
        .cancel_open_attempts_for_request(&input.request_id)
        .await?;
    service
        .request_store
        .finish_request(&input.request_id, RequestStatus::Cancelled)
        .await?;

    Ok(CancelUserRequestOutput {
        request_id: input.request_id,
        cancelled_agent_run_count: running.len(),
    })
}
