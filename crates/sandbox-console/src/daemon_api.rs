use std::sync::Arc;

use http::{Request, Response};
use hyper::body::Incoming;

use crate::endpoint;
use crate::proxy;
use crate::response::BoxBody;
use crate::state::AppState;

pub async fn handle(
    state: &Arc<AppState>,
    route: Route,
    req: Request<Incoming>,
) -> Response<BoxBody> {
    let endpoint = match endpoint::resolve(state, &route.sandbox_id).await {
        Ok(endpoint) => endpoint,
        Err(error) => return error.into_response(),
    };
    proxy::forward_to_endpoint(&endpoint, &route.target, req).await
}

pub struct Route {
    sandbox_id: String,
    target: String,
}

pub fn route(path: &str) -> Option<Route> {
    let rest = path.strip_prefix("/api/sandboxes/")?;
    let (sandbox_id, rest) = rest.split_once('/')?;
    if sandbox_id.is_empty() {
        return None;
    }
    let target = match (
        rest.strip_prefix("files/"),
        rest.strip_prefix("observability/"),
    ) {
        (Some(op), None) => format!("/files/{}", segment(op)?),
        (None, Some(view)) => format!("/observability/{}", segment(view)?),
        _ => return None,
    };
    Some(Route {
        sandbox_id: sandbox_id.to_owned(),
        target,
    })
}

fn segment(value: &str) -> Option<&str> {
    if value.is_empty() || value.contains('/') {
        return None;
    }
    Some(value)
}
