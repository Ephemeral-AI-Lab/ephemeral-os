//! List task-agent-runs for one user request.

use eos_types::{RequestId, TaskRun};

use crate::error::AgentCoreServerError;
use crate::service::AgentCoreService;

pub(crate) async fn list_user_request_tasks(
    service: &AgentCoreService,
    request_id: &RequestId,
) -> Result<Vec<TaskRun>, AgentCoreServerError> {
    if service.request_store.get(request_id).await?.is_none() {
        return Err(AgentCoreServerError::UserRequestNotFound(
            request_id.clone(),
        ));
    }
    Ok(service
        .task_agent_run_store
        .list_task_runs_for_request(request_id)
        .await?)
}
