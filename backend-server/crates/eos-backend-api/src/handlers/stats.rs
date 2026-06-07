//! `/api/stats` routes: performance, correctness, agent-runs, events. All read
//! `obs_event`/`audit_cursor` through the [`StatsReader`] facade, which keeps the
//! matched/unmatched daemon-audit split distinct from model-facing ids (AC7).

use axum::extract::{Query, State};
use axum::Json;

use eos_backend_types::{AgentRunStat, CorrectnessStats, ObsEvent, PageResult, PerformanceStats};

use super::Pagination;
use crate::error::ApiError;
use crate::router::AppState;

/// `GET /api/stats/performance` — tool-call timing and resource summaries.
pub async fn performance(
    State(state): State<AppState>,
) -> Result<Json<PerformanceStats>, ApiError> {
    Ok(Json(state.stats.performance().await?))
}

/// `GET /api/stats/correctness` — observed runs/tool-calls and the matched vs
/// unmatched daemon-audit split.
pub async fn correctness(
    State(state): State<AppState>,
) -> Result<Json<CorrectnessStats>, ApiError> {
    Ok(Json(state.stats.correctness().await?))
}

/// `GET /api/stats/agent-runs` — per-agent-run observability rollups.
pub async fn agent_runs(
    State(state): State<AppState>,
) -> Result<Json<Vec<AgentRunStat>>, ApiError> {
    Ok(Json(state.stats.agent_runs().await?))
}

/// `GET /api/stats/events` — a newest-first page of normalized observability
/// events (engine and daemon).
pub async fn events(
    State(state): State<AppState>,
    Query(pagination): Query<Pagination>,
) -> Result<Json<PageResult<ObsEvent>>, ApiError> {
    Ok(Json(state.stats.events(pagination.page()).await?))
}
