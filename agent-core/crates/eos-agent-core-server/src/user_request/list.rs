//! List user-request summaries.

use eos_types::{Page, PageResult, RequestListFilter};

use crate::dto::UserRequestSummary;
use crate::error::AgentCoreServerError;
use crate::request_state::RequestState;

pub(crate) async fn list_user_requests(
    state: &RequestState,
    page: Page,
) -> Result<PageResult<UserRequestSummary>, AgentCoreServerError> {
    let page = state
        .request_store
        .list(RequestListFilter::default(), page)
        .await?;
    Ok(PageResult {
        items: page
            .items
            .into_iter()
            .map(|request| UserRequestSummary {
                request_id: request.id,
                status: request.status,
                root_task_id: request.root_task_id,
                sandbox_id: request.sandbox_id,
                created_at: request.created_at,
                finished_at: request.finished_at,
            })
            .collect(),
        total: page.total,
    })
}
