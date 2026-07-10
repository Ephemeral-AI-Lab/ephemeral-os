//! HTTP/1.1 reverse proxy for one resolved forward target.
//!
//! Connects to the target, replays the request with the route prefix stripped
//! and `X-Forwarded-*` headers appended, streams both bodies, and tunnels
//! WebSocket/upgrade requests after the initial exchange. Connect and response
//! failures surface as [`ForwardError`] for the HTTP boundary to map.

use std::time::Duration;

use http::header::{HeaderMap, HeaderName, HeaderValue, CONNECTION, HOST, UPGRADE};
use http::{Method, Request, Response, StatusCode, Uri};
use http_body_util::BodyExt as _;
use hyper::body::Incoming;
use hyper_util::rt::TokioIo;
use tokio::net::TcpStream;
use tokio::time::timeout;

use super::route::ForwardRoute;
use super::{ForwardError, ForwardTarget};
use crate::http::response::{self, BoxBody};

const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
#[cfg(not(test))]
const RESPONSE_TIMEOUT: Duration = Duration::from_secs(30);
#[cfg(test)]
const RESPONSE_TIMEOUT: Duration = Duration::from_millis(100);

const HOP_BY_HOP: [&str; 7] = [
    "connection",
    "keep-alive",
    "proxy-connection",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
];

/// Forward one request to `target`, returning the upstream response (body
/// streamed) or a connect/timeout failure.
pub(crate) async fn run(
    target: &ForwardTarget,
    route: &ForwardRoute,
    req: Request<Incoming>,
) -> Result<Response<BoxBody>, ForwardError> {
    let stream = match timeout(
        CONNECT_TIMEOUT,
        TcpStream::connect((target.host.as_str(), target.port)),
    )
    .await
    {
        Ok(Ok(stream)) => stream,
        Ok(Err(_)) => return Err(ForwardError::Connect),
        Err(_) => return Err(ForwardError::Timeout),
    };
    let (mut sender, conn) =
        match hyper::client::conn::http1::handshake::<_, BoxBody>(TokioIo::new(stream)).await {
            Ok(pair) => pair,
            Err(_) => return Err(ForwardError::Connect),
        };
    tokio::spawn(async move {
        let _ = conn.with_upgrades().await;
    });

    if is_upgrade(req.headers()) {
        tunnel(&mut sender, route, req).await
    } else {
        forward_plain(&mut sender, route, req).await
    }
}

async fn forward_plain(
    sender: &mut hyper::client::conn::http1::SendRequest<BoxBody>,
    route: &ForwardRoute,
    req: Request<Incoming>,
) -> Result<Response<BoxBody>, ForwardError> {
    let (parts, body) = req.into_parts();
    let outbound = build_request(&parts.method, route, &parts.headers, body.boxed(), false);
    let upstream = send(sender, outbound).await?;
    Ok(relay_response(upstream))
}

async fn tunnel(
    sender: &mut hyper::client::conn::http1::SendRequest<BoxBody>,
    route: &ForwardRoute,
    mut req: Request<Incoming>,
) -> Result<Response<BoxBody>, ForwardError> {
    let outbound = build_request(req.method(), route, req.headers(), response::empty(), true);
    let mut upstream = send(sender, outbound).await?;
    if upstream.status() != StatusCode::SWITCHING_PROTOCOLS {
        return Ok(relay_response(upstream));
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
    Ok(Response::from_parts(parts, response::empty()))
}

async fn send(
    sender: &mut hyper::client::conn::http1::SendRequest<BoxBody>,
    request: Request<BoxBody>,
) -> Result<Response<Incoming>, ForwardError> {
    match timeout(RESPONSE_TIMEOUT, sender.send_request(request)).await {
        Ok(Ok(response)) => Ok(response),
        Ok(Err(_)) => Err(ForwardError::Connect),
        Err(_) => Err(ForwardError::Timeout),
    }
}

fn build_request(
    method: &Method,
    route: &ForwardRoute,
    src_headers: &HeaderMap,
    body: BoxBody,
    upgrade: bool,
) -> Request<BoxBody> {
    let mut request = Request::new(body);
    *request.method_mut() = method.clone();
    *request.uri_mut() = route
        .path_and_query()
        .parse::<Uri>()
        .unwrap_or_else(|_| Uri::from_static("/"));
    let headers = request.headers_mut();
    for (name, value) in src_headers {
        if upgrade || !is_hop_by_hop(name.as_str()) {
            headers.append(name.clone(), value.clone());
        }
    }
    if let Some(host) = src_headers.get(HOST) {
        headers.insert(HeaderName::from_static("x-forwarded-host"), host.clone());
    }
    headers.insert(
        HeaderName::from_static("x-forwarded-proto"),
        HeaderValue::from_static("http"),
    );
    if let Ok(prefix) = HeaderValue::from_str(&route.prefix()) {
        headers.insert(HeaderName::from_static("x-forwarded-prefix"), prefix);
    }
    request
}

fn relay_response(upstream: Response<Incoming>) -> Response<BoxBody> {
    let (mut parts, body) = upstream.into_parts();
    for name in HOP_BY_HOP {
        parts.headers.remove(name);
    }
    Response::from_parts(parts, body.boxed())
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
