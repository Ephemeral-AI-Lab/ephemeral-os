//! Top-level HTTP routing: dispatch exact `/health`, exact `/files/list`, and
//! `/forward/...` to their responders, and everything else to `404`. Route
//! *parsing* lives in `forward::route`; this module only chooses the responder
//! by path.

use std::sync::Arc;

use http::{Method, Request, Response, StatusCode};
use hyper::body::Incoming;

use super::response::{self, BoxBody};
use super::server::HttpState;
use super::{api, forward, health};

/// Dispatch one request to its responder.
pub(crate) async fn route(state: Arc<HttpState>, req: Request<Incoming>) -> Response<BoxBody> {
    let path = req.uri().path();
    if req.method() == Method::GET && path == "/health" {
        return health::respond(state.observer.sink_stats());
    }
    if path == "/files/list" {
        return api::handle(state, req).await;
    }
    if path.starts_with("/forward/") {
        return forward::handle(state, req).await;
    }
    response::text(StatusCode::NOT_FOUND, "not found")
}
