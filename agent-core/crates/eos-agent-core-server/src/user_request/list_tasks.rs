//! List task-agent-runs for one user request.

use eos_types::{RequestId, TaskRun};

use crate::error::AgentCoreServerError;
use crate::request_state::RequestState;

pub(crate) async fn list_user_request_tasks(
    state: &RequestState,
    request_id: &RequestId,
) -> Result<Vec<TaskRun>, AgentCoreServerError> {
    if state.request_store.get(request_id).await?.is_none() {
        return Err(AgentCoreServerError::UserRequestNotFound(
            request_id.clone(),
        ));
    }
    Ok(state
        .task_agent_run_store
        .list_task_runs_for_request(request_id)
        .await?)
}
