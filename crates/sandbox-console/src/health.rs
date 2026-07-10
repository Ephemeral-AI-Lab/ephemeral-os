//! `/api/sandboxes/:id/health`: resolve the sandbox record's `daemon_http`
//! endpoint and probe its `/health` with a short timeout. Probe outcomes are
//! HTTP 200 (`ok` / `unreachable`); resolution failures use the console
//! error mapping (404 / 503 / 502).

use http::{Request, StatusCode};
use http_body_util::BodyExt as _;
use hyper::Response;
use hyper_util::rt::TokioIo;
use serde_json::json;
use tokio::net::TcpStream;
use tokio::time::timeout;

use crate::endpoint::{self, HttpEndpoint};
use crate::response::{self, BoxBody};
use crate::state::AppState;

pub async fn handle(state: &AppState, sandbox_id: &str) -> Response<BoxBody> {
    let endpoint = match endpoint::resolve(state, sandbox_id).await {
        Ok(endpoint) => endpoint,
        Err(error) => return error.into_response(),
    };
    match timeout(state.config.health_probe_timeout, probe(&endpoint)).await {
        Ok(Ok(())) => response::json_value(StatusCode::OK, &json!({ "status": "ok" })),
        Ok(Err(detail)) => unreachable_response(&detail),
        Err(_) => unreachable_response("health probe timed out"),
    }
}

fn unreachable_response(detail: &str) -> Response<BoxBody> {
    response::json_value(
        StatusCode::OK,
        &json!({ "status": "unreachable", "detail": detail }),
    )
}

async fn probe(endpoint: &HttpEndpoint) -> Result<(), String> {
    let stream = TcpStream::connect((endpoint.host.as_str(), endpoint.port))
        .await
        .map_err(|error| format!("connect failed: {error}"))?;
    let (mut sender, connection) =
        hyper::client::conn::http1::handshake::<_, BoxBody>(TokioIo::new(stream))
            .await
            .map_err(|error| format!("handshake failed: {error}"))?;
    tokio::spawn(async move {
        let _ = connection.await;
    });
    let request = Request::builder()
        .uri("/health")
        .header(http::header::HOST, endpoint.host.as_str())
        .body(response::empty())
        .map_err(|error| format!("request build failed: {error}"))?;
    let upstream = sender
        .send_request(request)
        .await
        .map_err(|error| format!("request failed: {error}"))?;
    let status = upstream.status();
    let _ = upstream.into_body().collect().await;
    if status == StatusCode::OK {
        Ok(())
    } else {
        Err(format!("daemon_http /health answered {status}"))
    }
}
