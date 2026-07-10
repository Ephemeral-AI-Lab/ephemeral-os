//! `/forward` request flow: parse the route, resolve it to one TCP target,
//! proxy the request, and emit a single `daemon_http.forward` span. Shared and
//! isolated routes share this flow; only target resolution differs. Every
//! failure becomes a [`ForwardError`], mapped to its HTTP status once, here.

mod proxy;
mod route;

use std::sync::Arc;
use std::time::Instant;

use http::header::{HeaderMap, CONTENT_LENGTH};
use http::{Request, Response, StatusCode};
use hyper::body::Incoming;
use sandbox_observability::record::names;
use sandbox_observability::{Observer, SpanStatus, TraceContext};

use self::route::ForwardRoute;
use crate::http::response::{self, BoxBody};
use crate::http::server::HttpState;

/// A resolved forward destination: the host and port to dial. Resolution never
/// copies the request; `path_and_query` stays on the [`ForwardRoute`].
struct ForwardTarget {
    host: String,
    port: u16,
}

/// Every way a `/forward` request can fail. Route parsing, target resolution,
/// and proxying all report through this one vocabulary so the HTTP status,
/// span `error_kind`, and message are mapped in a single place.
pub(crate) enum ForwardError {
    InvalidRoute,
    InvalidPort,
    UnknownWorkspace,
    NoReachableIp,
    Connect,
    Timeout,
}

impl ForwardError {
    const fn status(&self) -> StatusCode {
        match self {
            Self::InvalidRoute | Self::InvalidPort => StatusCode::BAD_REQUEST,
            Self::UnknownWorkspace => StatusCode::NOT_FOUND,
            Self::NoReachableIp => StatusCode::FORBIDDEN,
            Self::Connect => StatusCode::BAD_GATEWAY,
            Self::Timeout => StatusCode::GATEWAY_TIMEOUT,
        }
    }

    const fn error_kind(&self) -> &'static str {
        match self {
            Self::InvalidRoute => "invalid_route",
            Self::InvalidPort => "invalid_port",
            Self::UnknownWorkspace => "unknown_workspace",
            Self::NoReachableIp => "no_reachable_ip",
            Self::Connect => "connect_failed",
            Self::Timeout => "timeout",
        }
    }

    const fn message(&self) -> &'static str {
        match self {
            Self::InvalidRoute => "invalid forward route",
            Self::InvalidPort => "invalid forward port",
            Self::UnknownWorkspace => "unknown isolated workspace",
            Self::NoReachableIp => "isolated workspace has no reachable IP",
            Self::Connect => "target connection failed",
            Self::Timeout => "target timed out",
        }
    }
}

/// Handle one `/forward` request end to end, recording its span on completion.
pub(crate) async fn handle(state: Arc<HttpState>, req: Request<Incoming>) -> Response<BoxBody> {
    let start = Instant::now();
    let (response, observation) = forward(&state, req).await;
    emit_span(&state.observer, observation, start.elapsed().as_millis());
    response
}

async fn forward(state: &HttpState, req: Request<Incoming>) -> (Response<BoxBody>, Observation) {
    let method = req.method().to_string();
    let bytes_in = content_length(req.headers());
    let route = match ForwardRoute::parse(req.uri()) {
        Ok(route) => route,
        Err(error) => {
            return (
                error_response(&error),
                Observation::failed(method, None, None, &error, bytes_in),
            )
        }
    };
    let target = match resolve(state, &route) {
        Ok(target) => target,
        Err(error) => {
            return (
                error_response(&error),
                Observation::failed(method, Some(&route), None, &error, bytes_in),
            )
        }
    };
    match proxy::run(&target, &route, req, state.config.forward_response_timeout).await {
        Ok(response) => {
            let status = response.status().as_u16();
            let bytes_out = content_length(response.headers());
            let observation = Observation::ok(method, &route, &target, status, bytes_in, bytes_out);
            (response, observation)
        }
        Err(error) => (
            error_response(&error),
            Observation::failed(method, Some(&route), Some(&target), &error, bytes_in),
        ),
    }
}

fn resolve(state: &HttpState, route: &ForwardRoute) -> Result<ForwardTarget, ForwardError> {
    match route {
        ForwardRoute::Shared { port, .. } => Ok(ForwardTarget {
            host: "127.0.0.1".to_owned(),
            port: *port,
        }),
        ForwardRoute::Isolated {
            workspace_id, port, ..
        } => match state.operations.workspace_session.isolated_ip(workspace_id) {
            Ok(Some(ip)) => Ok(ForwardTarget {
                host: ip.to_string(),
                port: *port,
            }),
            Ok(None) => Err(ForwardError::NoReachableIp),
            Err(_) => Err(ForwardError::UnknownWorkspace),
        },
    }
}

fn error_response(error: &ForwardError) -> Response<BoxBody> {
    response::text(error.status(), error.message())
}

fn content_length(headers: &HeaderMap) -> u64 {
    headers
        .get(CONTENT_LENGTH)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(0)
}

struct Observation {
    route_kind: &'static str,
    workspace_id: Option<String>,
    target_host: Option<String>,
    target_port: Option<u16>,
    method: String,
    path_prefix: String,
    status_code: u16,
    bytes_in: u64,
    bytes_out: u64,
    error_kind: Option<&'static str>,
}

impl Observation {
    fn ok(
        method: String,
        route: &ForwardRoute,
        target: &ForwardTarget,
        status_code: u16,
        bytes_in: u64,
        bytes_out: u64,
    ) -> Self {
        Self {
            route_kind: route.kind(),
            workspace_id: route.workspace_id().map(str::to_owned),
            target_host: Some(target.host.clone()),
            target_port: Some(target.port),
            method,
            path_prefix: route.prefix(),
            status_code,
            bytes_in,
            bytes_out,
            error_kind: None,
        }
    }

    fn failed(
        method: String,
        route: Option<&ForwardRoute>,
        target: Option<&ForwardTarget>,
        error: &ForwardError,
        bytes_in: u64,
    ) -> Self {
        Self {
            route_kind: route.map_or("invalid", ForwardRoute::kind),
            workspace_id: route
                .and_then(ForwardRoute::workspace_id)
                .map(str::to_owned),
            target_host: target.map(|target| target.host.clone()),
            target_port: target.map(|target| target.port),
            method,
            path_prefix: route.map_or_else(String::new, ForwardRoute::prefix),
            status_code: error.status().as_u16(),
            bytes_in,
            bytes_out: 0,
            error_kind: Some(error.error_kind()),
        }
    }
}

fn emit_span(observer: &Observer, observation: Observation, duration_ms: u128) {
    let trace_id = uuid::Uuid::new_v4().to_string();
    let context = TraceContext {
        trace: Arc::from(trace_id.as_str()),
        parent: None,
    };
    observer.with_context(Some(context), || {
        let span = observer.span(names::DAEMON_HTTP_FORWARD);
        span.attr("route_kind", observation.route_kind)
            .attr("method", observation.method)
            .attr("path_prefix", observation.path_prefix)
            .attr("status_code", observation.status_code)
            .attr(
                "duration_ms",
                u64::try_from(duration_ms).unwrap_or(u64::MAX),
            )
            .attr("bytes_in", observation.bytes_in)
            .attr("bytes_out", observation.bytes_out);
        if let Some(workspace_id) = observation.workspace_id {
            span.attr("workspace_id", workspace_id);
        }
        if let Some(target_host) = observation.target_host {
            span.attr("target_host", target_host);
        }
        if let Some(target_port) = observation.target_port {
            span.attr("target_port", target_port);
        }
        if let Some(error_kind) = observation.error_kind {
            span.attr("error_kind", error_kind)
                .status(SpanStatus::Error);
        }
    });
}
