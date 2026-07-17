//! `/health` responder. It reads only fixed-width telemetry atomics and never
//! touches runtime state or event storage.

use http::{Response, StatusCode};

use super::response::{self, BoxBody};

/// The `/health` response: `200` with liveness and fail-open drop counters.
pub(crate) fn respond(stats: sandbox_observability_telemetry::SinkStats) -> Response<BoxBody> {
    response::json_value(
        StatusCode::OK,
        &serde_json::json!({
            "status": "ok",
            "service": "daemon_http",
            "observability": stats,
        }),
    )
}
