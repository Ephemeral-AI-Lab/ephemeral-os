//! Top-level HTTP routing: dispatch exact `/health`, exact `/files/list`, and
//! `/forward/...` to their responders, and everything else to `404`. Route
//! *parsing* lives in `forward::route`; this module only chooses the responder
//! by path.

use std::sync::Arc;

use http::{Method, Request, Response, StatusCode};
use hyper::body::Incoming;
use tokio_util::task::TaskTracker;

use super::response::{self, BoxBody};
use super::server::HttpState;
use super::{api, forward, health};

/// Dispatch one request to its responder.
pub(crate) async fn route(
    state: Arc<HttpState>,
    child_tasks: TaskTracker,
    req: Request<Incoming>,
) -> Response<BoxBody> {
    if state.shutdown.is_cancelled() || state.blocking_admission.is_closed() {
        return response::json_value(
            StatusCode::SERVICE_UNAVAILABLE,
            &sandbox_operation_contract::error_response_with_details(
                "server_shutting_down",
                "daemon is shutting down",
                serde_json::json!({}),
            ),
        );
    }
    let path = req.uri().path();
    if req.method() == Method::GET && path == "/health" {
        return health::respond(state.observer.sink_stats());
    }
    if path == "/files/list" {
        return api::handle(state, req).await;
    }
    if path.starts_with("/forward/") {
        return forward::handle(state, child_tasks, req).await;
    }
    response::text(StatusCode::NOT_FOUND, "not found")
}
