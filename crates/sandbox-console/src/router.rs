//! Top-level HTTP routing: dispatch the six public console routes to their
//! responders and everything else to the SPA asset fallback.

use std::sync::Arc;

use http::{Method, Request, Response, StatusCode};
use hyper::body::Incoming;

use crate::response::{self, BoxBody};
use crate::state::AppState;
use crate::{assets, catalog, daemon_api, health, proxy, rpc};

pub async fn route(state: Arc<AppState>, req: Request<Incoming>) -> Response<BoxBody> {
    let path = req.uri().path().to_owned();
    if path == "/api/rpc" {
        if req.method() != Method::POST {
            return response::text(StatusCode::METHOD_NOT_ALLOWED, "use POST");
        }
        return rpc::handle(&state, req).await;
    }
    if path == "/api/catalog" {
        if req.method() != Method::GET {
            return response::text(StatusCode::METHOD_NOT_ALLOWED, "use GET");
        }
        return catalog::handle();
    }
    if let Some(route) = daemon_api::route(&path) {
        if req.method() != Method::POST {
            return response::text(StatusCode::METHOD_NOT_ALLOWED, "use POST");
        }
        return daemon_api::handle(&state, route, req).await;
    }
    if let Some(sandbox_id) = health_route(&path) {
        if req.method() != Method::GET {
            return response::text(StatusCode::METHOD_NOT_ALLOWED, "use GET");
        }
        return health::handle(&state, sandbox_id).await;
    }
    if path.starts_with("/s/") {
        return proxy::handle(&state, req).await;
    }
    if path.starts_with("/api/") {
        return response::text(StatusCode::NOT_FOUND, "unknown api route");
    }
    if req.method() != Method::GET && req.method() != Method::HEAD {
        return response::text(StatusCode::METHOD_NOT_ALLOWED, "use GET");
    }
    assets::serve(state.config.assets_dir.as_deref(), &path).await
}

fn health_route(path: &str) -> Option<&str> {
    let rest = path.strip_prefix("/api/sandboxes/")?;
    let sandbox_id = rest.strip_suffix("/health")?;
    if sandbox_id.is_empty() || sandbox_id.contains('/') {
        return None;
    }
    Some(sandbox_id)
}
