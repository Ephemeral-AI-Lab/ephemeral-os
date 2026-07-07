//! Top-level HTTP routing: dispatch the public console routes to their
//! responders and everything else to the SPA asset fallback.

use std::sync::Arc;

use http::{Method, Request, Response, StatusCode};
use hyper::body::Incoming;

use crate::assets;
use crate::response::{self, BoxBody};
use crate::state::AppState;

pub async fn route(state: Arc<AppState>, req: Request<Incoming>) -> Response<BoxBody> {
    let path = req.uri().path().to_owned();
    if path.starts_with("/api/") || path.starts_with("/s/") {
        return response::text(StatusCode::NOT_FOUND, "unknown api route");
    }
    if req.method() != Method::GET && req.method() != Method::HEAD {
        return response::text(StatusCode::METHOD_NOT_ALLOWED, "use GET");
    }
    assets::serve(state.config.assets_dir.as_deref(), &path).await
}
