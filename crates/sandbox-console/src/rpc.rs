//! `/api/rpc`: pass protocol requests through the gateway 1:1. The console
//! injects `request_id` and the auth token, never adding vocabulary. Protocol
//! errors return in the body with HTTP 200; transport failures map to
//! 400/502/504. With `Accept: text/event-stream` the request streams
//! `_stream_logs` progress as SSE `log` events followed by one `result`
//! event.

use std::sync::Arc;

use bytes::Bytes;
use http::header::{HeaderValue, ACCEPT, CACHE_CONTROL, CONTENT_TYPE};
use http::{HeaderMap, Request as HttpRequest, Response, StatusCode};
use http_body_util::BodyExt as _;
use hyper::body::Incoming;
use sandbox_protocol::{
    error_response_with_details, CliOperationScope, Request, MAX_REQUEST_BYTES,
};
use serde_json::{json, Value};

use crate::response::{self, BoxBody};
use crate::state::AppState;

pub async fn handle(state: &Arc<AppState>, req: HttpRequest<Incoming>) -> Response<BoxBody> {
    let wants_stream = accepts_event_stream(req.headers());
    let request = match read_request(req).await {
        Ok(request) => request,
        Err(response) => return response,
    };
    if wants_stream {
        stream(Arc::clone(state), request)
    } else {
        one_shot(state, request).await
    }
}

async fn one_shot(state: &AppState, request: Request) -> Response<BoxBody> {
    let sent = tokio::time::timeout(state.config.rpc_timeout, state.gateway.send(&request)).await;
    match sent {
        Ok(Ok(body)) => response::json_value(StatusCode::OK, &body),
        Ok(Err(error)) => {
            transport_error(StatusCode::BAD_GATEWAY, error.kind(), &error.to_string())
        }
        Err(_) => transport_error(
            StatusCode::GATEWAY_TIMEOUT,
            "gateway_timeout",
            "gateway did not answer within the rpc timeout",
        ),
    }
}

fn stream(state: Arc<AppState>, request: Request) -> Response<BoxBody> {
    let (sender, body) = response::channel_body();
    tokio::spawn(async move {
        let log_sender = sender.clone();
        let result = state
            .gateway
            .send_with_logs(&request, true, move |line| {
                let _ = log_sender.send(sse_event("log", &json!({ "line": line })));
            })
            .await;
        let final_event = match result {
            Ok(body) => sse_event("result", &body),
            Err(error) => sse_event(
                "error",
                &json!({ "kind": error.kind(), "message": error.to_string() }),
            ),
        };
        let _ = sender.send(final_event);
    });
    let mut response = Response::new(body);
    response.headers_mut().insert(
        CONTENT_TYPE,
        HeaderValue::from_static("text/event-stream; charset=utf-8"),
    );
    response
        .headers_mut()
        .insert(CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}

async fn read_request(req: HttpRequest<Incoming>) -> Result<Request, Response<BoxBody>> {
    let body = http_body_util::Limited::new(req.into_body(), MAX_REQUEST_BYTES);
    let bytes = match body.collect().await {
        Ok(collected) => collected.to_bytes(),
        Err(_) => {
            return Err(transport_error(
                StatusCode::BAD_REQUEST,
                "request_too_large",
                "request body exceeded the protocol size limit",
            ))
        }
    };
    let value: Value = serde_json::from_slice(&bytes).map_err(|error| {
        transport_error(
            StatusCode::BAD_REQUEST,
            "bad_json",
            &format!("request body is not valid json: {error}"),
        )
    })?;
    request_from_value(value)
        .map_err(|message| transport_error(StatusCode::BAD_REQUEST, "invalid_request", &message))
}

fn request_from_value(value: Value) -> Result<Request, String> {
    let Value::Object(mut object) = value else {
        return Err("request body must be a json object".to_owned());
    };
    let op = match object.remove("op") {
        Some(Value::String(op)) if !op.trim().is_empty() => op,
        _ => return Err("op is required and must be a non-empty string".to_owned()),
    };
    let scope = match object.remove("scope") {
        Some(scope) => serde_json::from_value::<CliOperationScope>(scope)
            .map_err(|error| format!("scope is invalid: {error}"))?,
        None => return Err("scope is required".to_owned()),
    };
    if scope.is_sandbox() && scope.sandbox_id().is_none_or(|id| id.trim().is_empty()) {
        return Err("scope sandbox_id must be non-empty".to_owned());
    }
    let args = match object.remove("args") {
        Some(args @ Value::Object(_)) => args,
        Some(_) => return Err("args must be an object".to_owned()),
        None => Value::Object(serde_json::Map::new()),
    };
    let request_id = match object.remove("request_id") {
        Some(Value::String(request_id)) if !request_id.trim().is_empty() => request_id,
        Some(_) => return Err("request_id must be a non-empty string".to_owned()),
        None => uuid::Uuid::new_v4().to_string(),
    };
    Ok(Request::new(op, request_id, scope, args))
}

fn transport_error(status: StatusCode, kind: &str, message: &str) -> Response<BoxBody> {
    response::json_value(
        status,
        &error_response_with_details(kind, message, json!({})),
    )
}

fn sse_event(event: &str, data: &Value) -> Bytes {
    let payload = serde_json::to_string(data).unwrap_or_default();
    Bytes::from(format!("event: {event}\ndata: {payload}\n\n"))
}

fn accepts_event_stream(headers: &HeaderMap) -> bool {
    headers
        .get(ACCEPT)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| {
            value
                .split(',')
                .any(|part| part.trim().starts_with("text/event-stream"))
        })
}
