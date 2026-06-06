//! `GET /api/user-requests/{request_id}/stream` — the live milestone stream.
//!
//! One route serves both transports: a WebSocket when the request carries an
//! upgrade, otherwise Server-Sent Events. Both replay persisted `event_log` rows
//! with `seq > last_seq` and then tail the live broadcast through one
//! [`EventBus::subscribe`] handoff with no gap (the replay/live join correctness
//! lives in `eos-backend-runtime`).
//!
//! axum 0.8 has no optional extractor for `WebSocketUpgrade`, so [`MaybeWs`]
//! wraps the optional extraction: it returns `Some` only when the request is a
//! genuine upgrade, and `None` (→ SSE) otherwise.

use std::convert::Infallible;

use axum::extract::ws::WebSocketUpgrade;
use axum::extract::{FromRequestParts, Path, Query, State};
use axum::http::request::Parts;
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

/// Optional WebSocket upgrade: `Some` for a genuine upgrade request, `None`
/// otherwise (so the same route falls back to SSE).
#[derive(Debug)]
pub struct MaybeWs(pub Option<WebSocketUpgrade>);

impl<S: Send + Sync> FromRequestParts<S> for MaybeWs {
    type Rejection = Infallible;

    async fn from_request_parts(parts: &mut Parts, state: &S) -> Result<Self, Self::Rejection> {
        // `WebSocketUpgrade` only reads (does not remove) the connection/upgrade
        // headers, so attempting it here is harmless when the request is plain SSE.
        Ok(Self(
            WebSocketUpgrade::from_request_parts(parts, state).await.ok(),
        ))
    }
}

/// Dispatch to WebSocket or SSE based on the presence of an upgrade. The SSE
/// `Last-Event-ID` reconnect header, when present, overrides `?last_seq=`.
pub async fn stream(
    State(state): State<AppState>,
    Path(request_id): Path<String>,
    Query(query): Query<StreamQuery>,
    headers: HeaderMap,
    MaybeWs(upgrade): MaybeWs,
) -> Result<Response, ApiError> {
    let request_id: RequestId = parse_id(&request_id, "request")?;
    if state.run_meta.get(&request_id).await?.is_none() {
        return Err(ApiError::NotFound("user request"));
    }
    let last_seq = last_event_id(&headers).or(query.last_seq).unwrap_or(0);

    match upgrade {
        Some(upgrade) => Ok(stream::ws::upgrade(upgrade, state, request_id, last_seq)),
        None => stream::sse::response(state, request_id, last_seq).await,
    }
}

/// Parse the SSE `Last-Event-ID` reconnect header into a sequence number.
fn last_event_id(headers: &HeaderMap) -> Option<i64> {
    headers.get("last-event-id")?.to_str().ok()?.parse().ok()
}
