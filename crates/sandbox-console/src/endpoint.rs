//! Sandbox `daemon_http` endpoint resolution over the gateway, with a
//! short-TTL cache so asset-heavy preview pages don't trigger a record
//! lookup per request. Health and preview share this one resolver and its
//! error vocabulary.

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use http::StatusCode;
use hyper::Response;
use sandbox_protocol::{error_kind, CliOperationScope, Request};
use serde_json::{json, Value};

use crate::response::{self, BoxBody};
use crate::state::AppState;

const SANDBOX_NOT_FOUND_MESSAGE: &str = "sandbox not found";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HttpEndpoint {
    pub host: String,
    pub port: u16,
}

#[derive(Debug)]
pub enum ResolveError {
    UnknownSandbox(String),
    NotReady(String),
    Gateway(String),
}

impl ResolveError {
    pub fn into_response(self) -> Response<BoxBody> {
        let (status, kind, message) = match self {
            Self::UnknownSandbox(id) => (
                StatusCode::NOT_FOUND,
                "unknown_sandbox",
                format!("unknown sandbox id: {id}"),
            ),
            Self::NotReady(id) => (
                StatusCode::SERVICE_UNAVAILABLE,
                "sandbox_not_ready",
                format!("sandbox {id} has no daemon_http endpoint yet"),
            ),
            Self::Gateway(message) => (StatusCode::BAD_GATEWAY, "gateway_error", message),
        };
        response::json_value(
            status,
            &sandbox_protocol::error_response_with_details(kind, message, json!({})),
        )
    }
}

#[derive(Debug)]
pub struct EndpointCache {
    entries: Mutex<HashMap<String, (Instant, HttpEndpoint)>>,
    ttl: Duration,
}

impl EndpointCache {
    #[must_use]
    pub fn new(ttl: Duration) -> Self {
        Self {
            entries: Mutex::new(HashMap::new()),
            ttl,
        }
    }

    fn get(&self, sandbox_id: &str) -> Option<HttpEndpoint> {
        let entries = self.entries.lock().ok()?;
        let (stored_at, endpoint) = entries.get(sandbox_id)?;
        if stored_at.elapsed() > self.ttl {
            return None;
        }
        Some(endpoint.clone())
    }

    fn put(&self, sandbox_id: &str, endpoint: HttpEndpoint) {
        if let Ok(mut entries) = self.entries.lock() {
            entries.insert(sandbox_id.to_owned(), (Instant::now(), endpoint));
        }
    }
}

/// Resolve the `daemon_http` endpoint for `sandbox_id` through the gateway's
/// `inspect_sandbox`, consulting the short-TTL cache first.
///
/// # Errors
/// Returns [`ResolveError`] when the sandbox is unknown, has no `daemon_http`
/// endpoint yet, or the gateway cannot be reached.
pub async fn resolve(state: &AppState, sandbox_id: &str) -> Result<HttpEndpoint, ResolveError> {
    if let Some(endpoint) = state.endpoints.get(sandbox_id) {
        return Ok(endpoint);
    }
    let request = Request::new(
        "inspect_sandbox",
        uuid::Uuid::new_v4().to_string(),
        CliOperationScope::system(),
        json!({ "sandbox_id": sandbox_id }),
    );
    let sent = tokio::time::timeout(
        state.config.endpoint_resolve_timeout,
        state.gateway.send(&request),
    )
    .await;
    let response = match sent {
        Ok(Ok(response)) => response,
        Ok(Err(error)) => return Err(ResolveError::Gateway(error.to_string())),
        Err(_) => return Err(ResolveError::Gateway("gateway timed out".to_owned())),
    };
    let endpoint = endpoint_from_record(sandbox_id, &response)?;
    state.endpoints.put(sandbox_id, endpoint.clone());
    Ok(endpoint)
}

fn endpoint_from_record(sandbox_id: &str, response: &Value) -> Result<HttpEndpoint, ResolveError> {
    if let Some(error) = response.get("error") {
        let kind = error.get("kind").and_then(Value::as_str).unwrap_or("");
        let message = error.get("message").and_then(Value::as_str).unwrap_or("");
        if kind == error_kind::INVALID_REQUEST && message.contains(SANDBOX_NOT_FOUND_MESSAGE) {
            return Err(ResolveError::UnknownSandbox(sandbox_id.to_owned()));
        }
        return Err(ResolveError::Gateway(format!(
            "inspect_sandbox failed: {message}"
        )));
    }
    let daemon_http = response.get("daemon_http").and_then(Value::as_object);
    let Some(daemon_http) = daemon_http else {
        return Err(ResolveError::NotReady(sandbox_id.to_owned()));
    };
    let host = daemon_http.get("host").and_then(Value::as_str);
    let port = daemon_http
        .get("port")
        .and_then(Value::as_u64)
        .and_then(|port| u16::try_from(port).ok());
    match (host, port) {
        (Some(host), Some(port)) if !host.is_empty() && port >= 1 => Ok(HttpEndpoint {
            host: host.to_owned(),
            port,
        }),
        _ => Err(ResolveError::NotReady(sandbox_id.to_owned())),
    }
}
