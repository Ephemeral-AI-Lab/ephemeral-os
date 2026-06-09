//! Query user-request state.

use eos_types::{AgentRun, RequestId};

use crate::dto::{UserRequestDetail, UserRequestSummary};
use crate::error::AgentCoreServerError;
use crate::service::AgentCoreService;

pub(crate) async fn read_user_request(
    service: &AgentCoreService,
    request_id: &RequestId,
) -> Result<Option<UserRequestDetail>, AgentCoreServerError> {
    Ok(service
        .request_store
        .get(request_id)
        .await?
        .map(|request| UserRequestDetail {
            request_id: request.id,
            status: request.status,
            sandbox_id: request.sandbox_id,
            prompt: request.request_prompt,
            created_at: request.created_at,
            updated_at: request.updated_at,
            finished_at: request.finished_at,
        }))
}

pub(crate) async fn list_user_requests(
    service: &AgentCoreService,
) -> Result<Vec<UserRequestSummary>, AgentCoreServerError> {
    let requests = service.request_store.list().await?;
    Ok(requests
        .into_iter()
        .map(|request| UserRequestSummary {
            request_id: request.id,
            status: request.status,
            sandbox_id: request.sandbox_id,
            created_at: request.created_at,
            finished_at: request.finished_at,
        })
        .collect())
}

pub(crate) async fn list_user_request_agent_runs(
    service: &AgentCoreService,
    request_id: &RequestId,
) -> Result<Vec<AgentRun>, AgentCoreServerError> {
    if service.request_store.get(request_id).await?.is_none() {
        return Err(AgentCoreServerError::UserRequestNotFound(
            request_id.clone(),
        ));
    }
    Ok(service
        .agent_run_store
        .list_agent_runs_for_request(request_id)
        .await?)
}
