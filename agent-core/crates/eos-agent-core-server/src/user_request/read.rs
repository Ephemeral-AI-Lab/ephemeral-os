//! Read one user request.

use eos_types::RequestId;

use crate::dto::UserRequestDetail;
use crate::error::AgentCoreServerError;
use crate::request_state::RequestState;

pub(crate) async fn read_user_request(
    state: &RequestState,
    request_id: &RequestId,
) -> Result<Option<UserRequestDetail>, AgentCoreServerError> {
    Ok(state
        .request_store
        .get(request_id)
        .await?
        .map(|request| UserRequestDetail {
            request_id: request.id,
            status: request.status,
            root_task_id: request.root_task_id,
            sandbox_id: request.sandbox_id,
            prompt: request.request_prompt,
            created_at: request.created_at,
            updated_at: request.updated_at,
            finished_at: request.finished_at,
        }))
}
