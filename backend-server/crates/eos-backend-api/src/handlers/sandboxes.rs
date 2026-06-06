//! `/api/sandboxes` routes: list, detail, delete — all over sanitized
//! [`SandboxView`]s, which carry no daemon connection material or credentials.

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::Json;

use eos_backend_types::SandboxView;
use eos_types::SandboxId;

use super::parse_id;
use crate::error::ApiError;
use crate::router::AppState;

/// `GET /api/sandboxes` — list backend-tracked sandboxes (sanitized views).
pub async fn list(State(state): State<AppState>) -> Json<Vec<SandboxView>> {
    Json(state.sandboxes.list())
}

/// `GET /api/sandboxes/{sandbox_id}` — one sanitized [`SandboxView`].
pub async fn detail(
    State(state): State<AppState>,
    Path(sandbox_id): Path<String>,
) -> Result<Json<SandboxView>, ApiError> {
    let sandbox_id: SandboxId = parse_id(&sandbox_id, "sandbox")?;
    state
        .sandboxes
        .view(&sandbox_id)
        .map(Json)
        .ok_or(ApiError::NotFound("sandbox"))
}

/// `DELETE /api/sandboxes/{sandbox_id}` — destroy a backend-owned sandbox.
/// `204` on success; `409` when active or retained; `404` when unknown. Never
/// requires or returns daemon auth material.
pub async fn delete(
    State(state): State<AppState>,
    Path(sandbox_id): Path<String>,
) -> Result<impl IntoResponse, ApiError> {
    let sandbox_id: SandboxId = parse_id(&sandbox_id, "sandbox")?;
    state.sandboxes.delete(&sandbox_id).await?;
    Ok(StatusCode::NO_CONTENT)
}
