//! `/health` responder. Returns a fixed JSON document and never reads runtime
//! state, so health stays a pure liveness signal for the HTTP listener.

use http::{Response, StatusCode};

use super::response::{self, BoxBody};

const HEALTH_BODY: &str = r#"{"status":"ok","service":"daemon_http"}"#;

/// The `/health` response: `200` with the fixed status document.
pub(crate) fn respond() -> Response<BoxBody> {
    response::json(StatusCode::OK, HEALTH_BODY)
}
