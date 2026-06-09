//! `/api/user-requests` routes: create, list, detail, cancel, events.

use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::Json;
use serde::Deserialize;

use eos_backend_runtime::{resolve_api_status, CancelOutcome};
use eos_backend_types::{
    BackendRunStatus, CreateUserRequest, CreateUserRequestResponse, EventRecord, PageResult,
    RunMeta, RunRecord, UserRequestDetail,
};
use eos_types::RequestStatus;
use eos_types::{RequestId, UtcDateTime};

use super::{parse_id, Pagination, ValidatedJson};
use crate::error::ApiError;
use crate::router::AppState;

/// Default cancellation reason recorded when a client cancels with no reason.
const DEFAULT_CANCEL_REASON: &str = "cancelled via api request";

/// `POST /api/user-requests` — accept a prompt and launch an agent-core run.
/// Returns `202 { request_id }`. v1 accepts only `sandbox_args.sandbox_id`;
/// unsupported override fields are rejected by `deny_unknown_fields` at
/// deserialize time.
pub async fn create(
    State(state): State<AppState>,
    ValidatedJson(request): ValidatedJson<CreateUserRequest>,
) -> Result<impl IntoResponse, ApiError> {
    let request_id = state.runs.launch(request).await.map_err(|err| {
        tracing::error!(error = %err, "run launch failed");
        ApiError::Internal
    })?;
    Ok((
        StatusCode::ACCEPTED,
        Json(CreateUserRequestResponse { request_id }),
    ))
}

/// `GET /api/user-requests` — list backend run records, newest first. Each row's
/// status comes from `run_meta` alone (the reaper finalizes it on completion), so
/// listing avoids a per-row agent-core join; the detail route does that join.
pub async fn list(
    State(state): State<AppState>,
    Query(pagination): Query<Pagination>,
) -> Result<Json<PageResult<RunRecord>>, ApiError> {
    let page = state.run_meta.list(pagination.page()).await?;
    let items = page.items.into_iter().map(run_record).collect();
    Ok(Json(PageResult {
        items,
        total: page.total,
        limit: page.limit,
        offset: page.offset,
    }))
}

/// `GET /api/user-requests/{request_id}` — backend lifecycle joined with the
/// agent-core request outcome through `RuntimeServices::state_reader()`. When the
/// backend row is still non-terminal but agent-core has finished, the resolved
/// terminal status is persisted with a CAS guard so the next read is stable
/// (and a concurrent cancellation is never clobbered).
pub async fn detail(
    State(state): State<AppState>,
    Path(request_id): Path<String>,
) -> Result<Json<UserRequestDetail>, ApiError> {
    let request_id: RequestId = parse_id(&request_id, "request")?;
    let meta = state
        .run_meta
        .get(&request_id)
        .await?
        .ok_or(ApiError::NotFound("user request"))?;
    let agent_status = state
        .reads
        .requests
        .get(&request_id)
        .await?
        .map(|request| request.status);

    let meta = reconcile(&state, &request_id, meta, agent_status).await?;
    let status = resolve_api_status(meta.status, agent_status);
    Ok(Json(UserRequestDetail {
        request_id: meta.request_id,
        status,
        label: meta.label,
        client_meta: meta.client_meta,
        created_at: meta.created_at,
        finished_at: meta.finished_at,
        cancel_reason: meta.cancel_reason,
    }))
}

/// Persist agent-core's terminal outcome onto a still-non-terminal backend row,
/// CAS-guarded. If the CAS matches nothing (a concurrent `DELETE` wrote
/// `cancelled` between the read and the update), re-read the authoritative row so
/// the response never reports `done`/`failed` over a just-written `cancelled`.
async fn reconcile(
    state: &AppState,
    request_id: &RequestId,
    meta: RunMeta,
    agent_status: Option<RequestStatus>,
) -> Result<RunMeta, ApiError> {
    let terminal = match (meta.status, agent_status) {
        (BackendRunStatus::Accepted | BackendRunStatus::Running, Some(RequestStatus::Done)) => {
            BackendRunStatus::Done
        }
        (BackendRunStatus::Accepted | BackendRunStatus::Running, Some(RequestStatus::Failed)) => {
            BackendRunStatus::Failed
        }
        _ => return Ok(meta),
    };
    match state
        .run_meta
        .reconcile_terminal(request_id, terminal, UtcDateTime::now())
        .await?
    {
        Some(updated) => Ok(updated),
        None => state
            .run_meta
            .get(request_id)
            .await?
            .ok_or(ApiError::NotFound("user request")),
    }
}

/// `DELETE /api/user-requests/{request_id}` — request backend-local cancellation.
/// `202` when an in-flight run was signalled; `409` when the run already
/// finalized; `404` when no such run exists.
pub async fn cancel(
    State(state): State<AppState>,
    Path(request_id): Path<String>,
) -> Result<impl IntoResponse, ApiError> {
    let request_id: RequestId = parse_id(&request_id, "request")?;
    if state.run_meta.get(&request_id).await?.is_none() {
        return Err(ApiError::NotFound("user request"));
    }
    match state.runs.cancel(&request_id, DEFAULT_CANCEL_REASON) {
        CancelOutcome::Requested => Ok(StatusCode::ACCEPTED),
        CancelOutcome::NotFound => Err(ApiError::Conflict("run already finished".to_owned())),
    }
}

/// `?after_seq=` query for the events replay route.
#[derive(Debug, Deserialize)]
pub struct EventsQuery {
    after_seq: Option<i64>,
}

/// `GET /api/user-requests/{request_id}/events` — replay persisted milestone
/// events from `event_log` (those with `seq > after_seq`), including any
/// `event_stream_gap` markers so dropped milestones stay visible.
pub async fn events(
    State(state): State<AppState>,
    Path(request_id): Path<String>,
    Query(query): Query<EventsQuery>,
) -> Result<Json<Vec<EventRecord>>, ApiError> {
    let request_id: RequestId = parse_id(&request_id, "request")?;
    if state.run_meta.get(&request_id).await?.is_none() {
        return Err(ApiError::NotFound("user request"));
    }
    let events = state
        .event_log
        .list_since(&request_id, query.after_seq.unwrap_or(0))
        .await?;
    Ok(Json(events))
}

/// Map a backend run row to its list record. Status resolves from `run_meta`
/// alone (no agent-core row passed), which for a finalized run is authoritative.
fn run_record(meta: RunMeta) -> RunRecord {
    RunRecord {
        request_id: meta.request_id,
        status: resolve_api_status(meta.status, None),
        label: meta.label,
        created_at: meta.created_at,
        finished_at: meta.finished_at,
    }
}
