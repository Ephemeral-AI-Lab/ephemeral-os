//! `GET /api/user-requests/{request_id}/stream` — the live milestone stream.
//!
//! The route serves Server-Sent Events only. It replays persisted `event_log`
//! rows with `seq > last_seq` and then tails the live broadcast through one
//! [`EventBus::subscribe`] handoff with no gap (the replay/live join correctness
//! lives in `eos-backend-runtime`).

use axum::extract::{Path, Query, State};
use axum::http::HeaderMap;
use axum::response::Response;
use serde::Deserialize;

use eos_types::RequestId;

use super::parse_id;
use crate::error::ApiError;
use crate::router::AppState;
use crate::stream;

/// `?last_seq=` query: replay resumes after this sequence (default `0` = start).
#[derive(Debug, Deserialize)]
pub struct StreamQuery {
    last_seq: Option<i64>,
}

/// Return an SSE stream. The `Last-Event-ID` reconnect header, when present,
/// overrides `?last_seq=`.
pub async fn stream(
    State(state): State<AppState>,
    Path(request_id): Path<String>,
    Query(query): Query<StreamQuery>,
    headers: HeaderMap,
) -> Result<Response, ApiError> {
    let request_id: RequestId = parse_id(&request_id, "request")?;
    if state.run_meta.get(&request_id).await?.is_none() {
        return Err(ApiError::NotFound("user request"));
    }
    let last_seq = last_event_id(&headers).or(query.last_seq).unwrap_or(0);

    stream::sse::response(state, request_id, last_seq).await
}

/// Parse the SSE `Last-Event-ID` reconnect header into a sequence number.
fn last_event_id(headers: &HeaderMap) -> Option<i64> {
    headers.get("last-event-id")?.to_str().ok()?.parse().ok()
}
