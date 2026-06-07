//! Agent-run node message-record routes.

use std::collections::VecDeque;
use std::convert::Infallible;
use std::time::Duration;

use axum::body::Body;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, Response};
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::IntoResponse;
use axum::Json;
use futures::stream;
use serde::Deserialize;

use eos_agent_message_records::{AgentMessageRecords, NodeEvent};
use eos_types::AgentRunId;

use super::parse_id;
use crate::error::ApiError;
use crate::router::AppState;

/// `?after_byte=` query for raw message JSONL tailing.
#[derive(Debug, Deserialize)]
pub struct MessagesQuery {
    after_byte: Option<u64>,
}

/// `?after_seq=` query for event replay.
#[derive(Debug, Deserialize)]
pub struct EventsQuery {
    after_seq: Option<u64>,
}

/// `?last_seq=` query for SSE replay.
#[derive(Debug, Deserialize)]
pub struct StreamQuery {
    last_seq: Option<u64>,
}

/// `GET /api/agent-runs/{agent_run_id}/messages`.
pub async fn messages(
    State(state): State<AppState>,
    Path(agent_run_id): Path<String>,
    Query(query): Query<MessagesQuery>,
) -> Result<Response<Body>, ApiError> {
    let agent_run_id: AgentRunId = parse_id(&agent_run_id, "agent run")?;
    let bytes = state
        .message_records
        .read_messages(&agent_run_id, query.after_byte.unwrap_or(0))
        .await?;
    Response::builder()
        .header("content-type", "application/x-ndjson")
        .header("x-next-byte-offset", bytes.next_byte_offset.to_string())
        .body(Body::from(bytes.bytes))
        .map_err(|err| {
            tracing::error!(error = %err, "failed to build messages response");
            ApiError::Internal
        })
}

/// `GET /api/agent-runs/{agent_run_id}/events`.
pub async fn events(
    State(state): State<AppState>,
    Path(agent_run_id): Path<String>,
    Query(query): Query<EventsQuery>,
) -> Result<Json<Vec<NodeEvent>>, ApiError> {
    let agent_run_id: AgentRunId = parse_id(&agent_run_id, "agent run")?;
    Ok(Json(
        state
            .message_records
            .read_events(&agent_run_id, query.after_seq.unwrap_or(0))
            .await?,
    ))
}

/// `GET /api/agent-runs/{agent_run_id}/stream`.
pub async fn stream(
    State(state): State<AppState>,
    Path(agent_run_id): Path<String>,
    Query(query): Query<StreamQuery>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, ApiError> {
    let agent_run_id: AgentRunId = parse_id(&agent_run_id, "agent run")?;
    let last_seq = last_event_id(&headers).or(query.last_seq).unwrap_or(0);
    let initial = state
        .message_records
        .read_events(&agent_run_id, last_seq)
        .await?;
    let tail = TailState::new(
        state.message_records,
        agent_run_id,
        last_seq,
        VecDeque::from(initial),
    );
    let events = stream::unfold(tail, |mut tail| async move {
        loop {
            if let Some(event) = tail.pending.pop_front() {
                tail.next_seq = tail.next_seq.max(event.seq);
                if event.kind == "node_finished" {
                    tail.finished = true;
                }
                return Some((Ok::<Event, Infallible>(to_sse_event(&event)), tail));
            }
            if tail.finished {
                return None;
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
            match tail
                .message_records
                .read_events(&tail.agent_run_id, tail.next_seq)
                .await
            {
                Ok(events) => {
                    tail.pending = VecDeque::from(events);
                }
                Err(err) => {
                    tracing::error!(error = %err, "agent-run SSE message-record tail failed");
                    return None;
                }
            }
        }
    });
    Ok(Sse::new(events).keep_alive(KeepAlive::default()))
}

#[derive(Debug)]
struct TailState {
    message_records: AgentMessageRecords,
    agent_run_id: AgentRunId,
    next_seq: u64,
    pending: VecDeque<NodeEvent>,
    finished: bool,
}

impl TailState {
    fn new(
        message_records: AgentMessageRecords,
        agent_run_id: AgentRunId,
        last_seq: u64,
        pending: VecDeque<NodeEvent>,
    ) -> Self {
        Self {
            message_records,
            agent_run_id,
            next_seq: last_seq,
            pending,
            finished: false,
        }
    }
}

fn to_sse_event(event: &NodeEvent) -> Event {
    let payload = serde_json::to_string(&event.payload).unwrap_or_else(|err| {
        tracing::error!(error = %err, "failed to encode SSE event payload");
        "{}".to_owned()
    });
    Event::default()
        .id(event.seq.to_string())
        .event(&event.kind)
        .data(payload)
}

fn last_event_id(headers: &HeaderMap) -> Option<u64> {
    headers.get("last-event-id")?.to_str().ok()?.parse().ok()
}
