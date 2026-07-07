//! `/s/:id/...` preview proxy: one prefix swap onto the sandbox's
//! `daemon_http` `/forward/...`, preserving method, headers, query, and
//! streamed bodies, tunneling WebSocket/HTTP upgrades, and appending
//! `X-Forwarded-For`. `daemon_http` error responses pass through verbatim.

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use http::header::{HeaderMap, HeaderName, HeaderValue, CONNECTION, UPGRADE};
use http::{Request, Response, StatusCode, Uri};
use http_body_util::BodyExt as _;
use hyper::body::Incoming;
use hyper_util::rt::TokioIo;
use tokio::net::TcpStream;
use tokio::time::timeout;

use crate::endpoint::{self, HttpEndpoint};
use crate::response::{self, BoxBody};
use crate::state::AppState;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const RESPONSE_TIMEOUT: Duration = Duration::from_secs(30);

const HOP_BY_HOP: [&str; 7] = [
    "connection",
    "keep-alive",
    "proxy-connection",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
];

const X_FORWARDED_FOR: HeaderName = HeaderName::from_static("x-forwarded-for");

pub async fn handle(state: &Arc<AppState>, req: Request<Incoming>) -> Response<BoxBody> {
    let route = match PreviewRoute::parse(req.uri()) {
        Ok(route) => route,
        Err(message) => return response::text(StatusCode::BAD_REQUEST, message),
    };
    let endpoint = match endpoint::resolve(state, &route.sandbox_id).await {
        Ok(endpoint) => endpoint,
        Err(error) => return error.into_response(),
    };
    forward_to_endpoint(&endpoint, &route.forward_target, req).await
}

/// A parsed `/s/<id>/...` route: the sandbox id plus the daemon-side
/// `/forward/...` request target after the prefix swap.
struct PreviewRoute {
    sandbox_id: String,
    forward_target: String,
}

impl PreviewRoute {
    fn parse(uri: &Uri) -> Result<Self, &'static str> {
        let rest = uri
            .path()
            .strip_prefix("/s/")
            .ok_or("invalid preview route")?;
        let (sandbox_id, scope_rest) = rest.split_once('/').ok_or("invalid preview route")?;
        if sandbox_id.is_empty() {
            return Err("invalid preview route");
        }
        validate_scope(scope_rest)?;
        let mut forward_target = String::with_capacity(9 + scope_rest.len());
        forward_target.push_str("/forward/");
        forward_target.push_str(scope_rest);
        if let Some(query) = uri.query() {
            forward_target.push('?');
            forward_target.push_str(query);
        }
        Ok(Self {
            sandbox_id: sandbox_id.to_owned(),
            forward_target,
        })
    }
}

fn validate_scope(scope_rest: &str) -> Result<(), &'static str> {
    let port_segment = if let Some(shared) = scope_rest.strip_prefix("shared/") {
        shared
    } else if let Some(isolated) = scope_rest.strip_prefix("isolated=") {
        let (workspace_id, after) = isolated.split_once('/').ok_or("invalid preview route")?;
        if workspace_id.is_empty() {
            return Err("invalid preview route");
        }
        after
    } else {
        return Err("invalid preview route");
    };
    let port = port_segment.split('/').next().unwrap_or("");
    match port.parse::<u16>() {
        Ok(port) if port >= 1 => Ok(()),
        _ => Err("invalid preview port"),
    }
}

pub async fn forward_to_endpoint(
    endpoint: &HttpEndpoint,
    target: &str,
    req: Request<Incoming>,
) -> Response<BoxBody> {
    let stream = match timeout(
        CONNECT_TIMEOUT,
        TcpStream::connect((endpoint.host.as_str(), endpoint.port)),
    )
    .await
    {
        Ok(Ok(stream)) => stream,
        Ok(Err(_)) => return unreachable_response("daemon_http connection failed"),
        Err(_) => return timeout_response(),
    };
    let (mut sender, connection) =
        match hyper::client::conn::http1::handshake::<_, BoxBody>(TokioIo::new(stream)).await {
            Ok(pair) => pair,
            Err(_) => return unreachable_response("daemon_http handshake failed"),
        };
    tokio::spawn(async move {
        let _ = connection.with_upgrades().await;
    });

    if is_upgrade(req.headers()) {
        tunnel(&mut sender, target, req).await
    } else {
        forward_plain(&mut sender, target, req).await
    }
}

async fn forward_plain(
    sender: &mut hyper::client::conn::http1::SendRequest<BoxBody>,
    target: &str,
    req: Request<Incoming>,
) -> Response<BoxBody> {
    let (parts, body) = req.into_parts();
    let peer = parts.extensions.get::<SocketAddr>().copied();
    let outbound = build_request(
        &parts.method,
        target,
        &parts.headers,
        peer,
        body.boxed(),
        false,
    );
    match send(sender, outbound).await {
        Ok(upstream) => relay_response(upstream),
        Err(response) => response,
    }
}

async fn tunnel(
    sender: &mut hyper::client::conn::http1::SendRequest<BoxBody>,
    target: &str,
    mut req: Request<Incoming>,
) -> Response<BoxBody> {
    let peer = req.extensions().get::<SocketAddr>().copied();
    let outbound = build_request(
        req.method(),
        target,
        req.headers(),
        peer,
        response::empty(),
        true,
    );
    let mut upstream = match send(sender, outbound).await {
        Ok(upstream) => upstream,
        Err(response) => return response,
    };
    if upstream.status() != StatusCode::SWITCHING_PROTOCOLS {
        return relay_response(upstream);
    }
    let upstream_upgrade = hyper::upgrade::on(&mut upstream);
    let client_upgrade = hyper::upgrade::on(&mut req);
    tokio::spawn(async move {
        if let (Ok(client), Ok(server)) = tokio::join!(client_upgrade, upstream_upgrade) {
            let mut client = TokioIo::new(client);
            let mut server = TokioIo::new(server);
            let _ = tokio::io::copy_bidirectional(&mut client, &mut server).await;
        }
    });
    let (parts, _body) = upstream.into_parts();
    Response::from_parts(parts, response::empty())
}

async fn send(
    sender: &mut hyper::client::conn::http1::SendRequest<BoxBody>,
    request: Request<BoxBody>,
) -> Result<Response<Incoming>, Response<BoxBody>> {
    match timeout(RESPONSE_TIMEOUT, sender.send_request(request)).await {
        Ok(Ok(response)) => Ok(response),
        Ok(Err(_)) => Err(unreachable_response("daemon_http request failed")),
        Err(_) => Err(timeout_response()),
    }
}

fn build_request(
    method: &http::Method,
    target: &str,
    src_headers: &HeaderMap,
    peer: Option<SocketAddr>,
    body: BoxBody,
    upgrade: bool,
) -> Request<BoxBody> {
    let mut request = Request::new(body);
    *request.method_mut() = method.clone();
    *request.uri_mut() = target
        .parse::<Uri>()
        .unwrap_or_else(|_| Uri::from_static("/"));
    let headers = request.headers_mut();
    for (name, value) in src_headers {
        if upgrade || !is_hop_by_hop(name.as_str()) {
            headers.append(name.clone(), value.clone());
        }
    }
    if let Some(peer) = peer {
        append_forwarded_for(headers, peer);
    }
    request
}

fn append_forwarded_for(headers: &mut HeaderMap, peer: SocketAddr) {
    let peer_ip = peer.ip().to_string();
    let value = match headers
        .get(&X_FORWARDED_FOR)
        .and_then(|value| value.to_str().ok())
    {
        Some(existing) => format!("{existing}, {peer_ip}"),
        None => peer_ip,
    };
    if let Ok(value) = HeaderValue::from_str(&value) {
        headers.insert(X_FORWARDED_FOR, value);
    }
}

fn relay_response(upstream: Response<Incoming>) -> Response<BoxBody> {
    let (mut parts, body) = upstream.into_parts();
    for name in HOP_BY_HOP {
        parts.headers.remove(name);
    }
    Response::from_parts(parts, body.boxed())
}

fn unreachable_response(detail: &'static str) -> Response<BoxBody> {
    response::text(StatusCode::BAD_GATEWAY, detail)
}

fn timeout_response() -> Response<BoxBody> {
    response::text(StatusCode::GATEWAY_TIMEOUT, "daemon_http timed out")
}

fn is_upgrade(headers: &HeaderMap) -> bool {
    headers.contains_key(UPGRADE)
        && headers
            .get(CONNECTION)
            .and_then(|value| value.to_str().ok())
            .is_some_and(|value| {
                value
                    .split(',')
                    .any(|token| token.trim().eq_ignore_ascii_case("upgrade"))
            })
}

fn is_hop_by_hop(name: &str) -> bool {
    HOP_BY_HOP.contains(&name)
}
