use std::sync::Arc;

use http::{Method, Request as HttpRequest, Response, StatusCode};
use http_body_util::BodyExt as _;
use hyper::body::Incoming;
use sandbox_observability_telemetry::record::names;
use sandbox_observability_telemetry::{SpanStatus, TraceContext};
use sandbox_operation_contract::{error, OperationRequest, OperationScope};
use sandbox_protocol::error::{BAD_JSON, REQUEST_TOO_LARGE};
use serde_json::{json, Map, Value};

use super::response::{self, BoxBody};
use super::server::HttpState;

pub(crate) async fn handle(state: Arc<HttpState>, req: HttpRequest<Incoming>) -> Response<BoxBody> {
    if req.method() != Method::POST {
        return response::text(StatusCode::METHOD_NOT_ALLOWED, "use POST");
    }
    handle_file_list(state, req).await
}

async fn handle_file_list(state: Arc<HttpState>, req: HttpRequest<Incoming>) -> Response<BoxBody> {
    let args = match read_args(req, state.config.limits.max_request_bytes).await {
        Ok(args) => args,
        Err(response) => return response,
    };
    let request = protocol_request(&state, Value::Object(args));
    let operations = Arc::clone(&state.operations);
    let observer = state.observer.clone();
    let task = tokio::task::spawn_blocking(move || {
        let ctx = TraceContext {
            trace: Arc::from(request.request_id.as_str()),
            parent: None,
        };
        observer.with_context(ctx, || {
            let dispatch = observer.span(names::DAEMON_DISPATCH);
            dispatch.attr("op", request.op.clone());
            let json = sandbox_runtime::dispatch_operation(&operations, &request).into_json_value();
            if json.get("error").is_some() {
                dispatch.status(SpanStatus::Error);
            }
            json
        })
    });
    match task.await {
        Ok(value) => response::json_value(StatusCode::OK, &value),
        Err(err) if err.is_cancelled() => protocol_error("daemon request cancelled"),
        Err(err) => protocol_error(&format!("daemon request failed: {err}")),
    }
}

async fn read_args(
    req: HttpRequest<Incoming>,
    max_request_bytes: usize,
) -> Result<Map<String, Value>, Response<BoxBody>> {
    let body = http_body_util::Limited::new(req.into_body(), max_request_bytes);
    let bytes = match body.collect().await {
        Ok(collected) => collected.to_bytes(),
        Err(_) => {
            return Err(transport_error(
                StatusCode::BAD_REQUEST,
                REQUEST_TOO_LARGE,
                "request body exceeded the protocol size limit",
            ))
        }
    };
    if bytes.is_empty() {
        return Ok(Map::new());
    }
    match serde_json::from_slice::<Value>(&bytes) {
        Ok(Value::Object(args)) => Ok(args),
        Ok(_) => Err(transport_error(
            StatusCode::BAD_REQUEST,
            error::INVALID_REQUEST,
            "request body must be a json object",
        )),
        Err(error) => Err(transport_error(
            StatusCode::BAD_REQUEST,
            BAD_JSON,
            &format!("request body is not valid json: {error}"),
        )),
    }
}

fn protocol_request(state: &HttpState, args: Value) -> OperationRequest {
    OperationRequest::new(
        sandbox_operation_catalog::internal::runtime::FILE_LIST,
        uuid::Uuid::new_v4().to_string(),
        OperationScope::sandbox(state.sandbox_id()),
        args,
    )
}

fn transport_error(status: StatusCode, kind: &str, message: &str) -> Response<BoxBody> {
    response::json_value(
        status,
        &sandbox_operation_contract::error_response_with_details(kind, message, json!({})),
    )
}

fn protocol_error(message: &str) -> Response<BoxBody> {
    response::json_value(
        StatusCode::OK,
        &sandbox_operation_contract::error_response_with_details(
            error::INTERNAL_ERROR,
            message,
            json!({}),
        ),
    )
}
