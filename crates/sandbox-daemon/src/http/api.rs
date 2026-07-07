use std::sync::Arc;

use http::{Method, Request as HttpRequest, Response, StatusCode};
use http_body_util::BodyExt as _;
use hyper::body::Incoming;
use sandbox_observability::record::names;
use sandbox_observability::{SpanStatus, TraceContext};
use sandbox_protocol::{error_kind, CliOperationScope, Request, MAX_REQUEST_BYTES};
use serde_json::{json, Map, Value};

use super::response::{self, BoxBody};
use super::server::HttpState;

pub(crate) async fn handle(state: Arc<HttpState>, req: HttpRequest<Incoming>) -> Response<BoxBody> {
    let path = req.uri().path().to_owned();
    if req.method() != Method::POST {
        return response::text(StatusCode::METHOD_NOT_ALLOWED, "use POST");
    }
    if let Some(op) = file_op(&path) {
        return handle_file(state, op, req).await;
    }
    if let Some(view) = observability_view(&path) {
        return handle_observability(state, view, req).await;
    }
    response::text(StatusCode::NOT_FOUND, "unknown api route")
}

async fn handle_file(
    state: Arc<HttpState>,
    op: &'static str,
    req: HttpRequest<Incoming>,
) -> Response<BoxBody> {
    let args = match read_args(req).await {
        Ok(args) => args,
        Err(response) => return response,
    };
    let request = protocol_request(&state, op, Value::Object(args));
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
        Ok(value) => {
            collect_observability(&state);
            response::json_value(StatusCode::OK, &value)
        }
        Err(err) if err.is_cancelled() => protocol_error("daemon request cancelled"),
        Err(err) => protocol_error(&format!("daemon request failed: {err}")),
    }
}

async fn handle_observability(
    state: Arc<HttpState>,
    view: String,
    req: HttpRequest<Incoming>,
) -> Response<BoxBody> {
    let mut args = match read_args(req).await {
        Ok(args) => args,
        Err(response) => return response,
    };
    args.insert("view".to_owned(), Value::String(view));
    let request = protocol_request(&state, "get_observability", Value::Object(args));
    let operations = Arc::clone(&state.operations);
    let observability = state.observability.clone();
    let task = tokio::task::spawn_blocking(move || {
        crate::observability::observability_view_response(
            &operations,
            observability.as_deref(),
            &request,
        )
        .into_json_value()
    });
    match task.await {
        Ok(value) => response::json_value(StatusCode::OK, &value),
        Err(err) if err.is_cancelled() => protocol_error("daemon observability request cancelled"),
        Err(err) => protocol_error(&format!("daemon observability request failed: {err}")),
    }
}

async fn read_args(req: HttpRequest<Incoming>) -> Result<Map<String, Value>, Response<BoxBody>> {
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
    if bytes.is_empty() {
        return Ok(Map::new());
    }
    match serde_json::from_slice::<Value>(&bytes) {
        Ok(Value::Object(args)) => Ok(args),
        Ok(_) => Err(transport_error(
            StatusCode::BAD_REQUEST,
            error_kind::INVALID_REQUEST,
            "request body must be a json object",
        )),
        Err(error) => Err(transport_error(
            StatusCode::BAD_REQUEST,
            error_kind::BAD_JSON,
            &format!("request body is not valid json: {error}"),
        )),
    }
}

fn protocol_request(state: &HttpState, op: &str, args: Value) -> Request {
    Request::new(
        op,
        uuid::Uuid::new_v4().to_string(),
        CliOperationScope::sandbox(state.sandbox_id()),
        args,
    )
}

fn collect_observability(state: &Arc<HttpState>) {
    let Some(observability) = state.observability.clone() else {
        return;
    };
    let config = state.config.clone();
    let operations = Arc::clone(&state.operations);
    let handle = tokio::task::spawn_blocking(move || {
        observability.collect(&config, &operations);
    });
    drop(handle);
}

fn file_op(path: &str) -> Option<&'static str> {
    match path.strip_prefix("/files/")? {
        "list" => Some("file_list"),
        "read" => Some("file_read"),
        "write" => Some("file_write"),
        "edit" => Some("file_edit"),
        "blame" => Some("file_blame"),
        _ => None,
    }
}

fn observability_view(path: &str) -> Option<String> {
    let view = path.strip_prefix("/observability/")?;
    if view.is_empty() || view.contains('/') {
        return None;
    }
    Some(view.to_owned())
}

fn transport_error(status: StatusCode, kind: &str, message: &str) -> Response<BoxBody> {
    response::json_value(
        status,
        &sandbox_protocol::error_response_with_details(kind, message, json!({})),
    )
}

fn protocol_error(message: &str) -> Response<BoxBody> {
    response::json_value(
        StatusCode::OK,
        &sandbox_protocol::error_response_with_details(
            error_kind::INTERNAL_ERROR,
            message,
            json!({}),
        ),
    )
}
