use std::io::{BufRead, BufReader, Read};
use std::time::Duration;

use serde_json::{json, Map, Value};

pub(crate) const REQUEST_READ_TIMEOUT: Duration = Duration::from_secs(30);
const MAX_REQUEST_BYTES: usize = host::MAX_REQUEST_BYTES;
const ENVELOPE_VERSION: u8 = 2;

pub(crate) mod error_kind {
    pub(crate) const BAD_JSON: &str = "bad_json";
    pub(crate) const FORBIDDEN: &str = "forbidden";
    pub(crate) const HOST_OPERATION_FAILED: &str = "host_operation_failed";
    pub(crate) const INVALID_REQUEST: &str = "invalid_request";
    pub(crate) const REQUEST_TOO_LARGE: &str = "request_too_large";
    pub(crate) const SANDBOX_UNAVAILABLE: &str = "sandbox_unavailable";
    pub(crate) const SERVER_BUSY: &str = "server_busy";
    pub(crate) const UNCERTAIN_OUTCOME: &str = "uncertain_outcome";
    pub(crate) const UNKNOWN_OP: &str = "unknown_op";
    pub(crate) const UNKNOWN_SANDBOX: &str = "unknown_sandbox";
}

#[derive(Debug)]
pub(crate) struct ClientRequest {
    pub(crate) op: String,
    pub(crate) sandbox_id: Option<String>,
    pub(crate) request_id: String,
    pub(crate) args: Value,
}

#[derive(Debug)]
pub(crate) struct WireError {
    pub(crate) kind: &'static str,
    pub(crate) message: String,
    pub(crate) sandbox_id: Option<String>,
}

impl WireError {
    fn new(kind: &'static str, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
            sandbox_id: None,
        }
    }

    fn with_sandbox(mut self, sandbox_id: Option<&str>) -> Self {
        self.sandbox_id = sandbox_id.map(ToOwned::to_owned);
        self
    }
}

pub(crate) fn read_request_line(stream: impl Read) -> Result<Vec<u8>, WireError> {
    let mut reader = BufReader::new(stream.take(MAX_REQUEST_BYTES as u64 + 1));
    let mut line = Vec::new();
    reader.read_until(b'\n', &mut line).map_err(|err| {
        WireError::new(error_kind::INVALID_REQUEST, format!("read request: {err}"))
    })?;
    if line.is_empty() {
        return Err(WireError::new(
            error_kind::INVALID_REQUEST,
            "connection closed before a request line",
        ));
    }
    if line.len() > MAX_REQUEST_BYTES {
        return Err(WireError::new(
            error_kind::REQUEST_TOO_LARGE,
            format!("request exceeds {MAX_REQUEST_BYTES} bytes"),
        ));
    }
    Ok(line)
}

pub(crate) fn parse_request(line: &[u8]) -> Result<ClientRequest, WireError> {
    let value: Value = serde_json::from_slice(line).map_err(|err| {
        WireError::new(
            error_kind::BAD_JSON,
            format!("request is not valid JSON: {err}"),
        )
    })?;
    let Value::Object(mut object) = value else {
        return Err(WireError::new(
            error_kind::INVALID_REQUEST,
            "request must be a JSON object",
        ));
    };
    let sandbox_id = match object.remove("sandbox_id") {
        None | Some(Value::Null) => None,
        Some(Value::String(id)) => Some(id),
        Some(_) => {
            return Err(WireError::new(
                error_kind::INVALID_REQUEST,
                "sandbox_id must be a string",
            ))
        }
    };
    let op =
        take_string(&mut object, "op").map_err(|err| err.with_sandbox(sandbox_id.as_deref()))?;
    if op.trim().is_empty() {
        return Err(
            WireError::new(error_kind::INVALID_REQUEST, "op is required")
                .with_sandbox(sandbox_id.as_deref()),
        );
    }
    let request_id = take_string(&mut object, "request_id")
        .map_err(|err| err.with_sandbox(sandbox_id.as_deref()))?;
    let args = object.remove("args").unwrap_or_else(|| json!({}));
    if !args.is_object() {
        return Err(
            WireError::new(error_kind::INVALID_REQUEST, "args must be an object")
                .with_sandbox(sandbox_id.as_deref()),
        );
    }
    Ok(ClientRequest {
        op,
        sandbox_id,
        request_id,
        args,
    })
}

fn take_string(object: &mut Map<String, Value>, field: &str) -> Result<String, WireError> {
    match object.remove(field) {
        Some(Value::String(value)) => Ok(value),
        _ => Err(WireError::new(
            error_kind::INVALID_REQUEST,
            format!("{field} is required and must be a string"),
        )),
    }
}

pub(crate) fn ok_response(request: &ClientRequest, result: Value) -> Value {
    let mut response = envelope_base("ok", request_meta(request));
    response["result"] = result;
    response
}

pub(crate) fn error_response_for(request: &ClientRequest, kind: &str, message: &str) -> Value {
    error_response_with_meta(kind, message, request_meta(request))
}

pub(crate) fn error_response(kind: &str, message: &str) -> Value {
    error_response_with_meta(kind, message, bare_meta())
}

pub(crate) fn server_busy_response(max_concurrent_connections: usize) -> Value {
    let mut response = error_response(error_kind::SERVER_BUSY, "gateway is at connection capacity");
    response["error"]["details"] =
        json!({"max_concurrent_connections": max_concurrent_connections});
    response
}

fn error_response_with_meta(kind: &str, message: &str, meta: Value) -> Value {
    let mut response = envelope_base("error", meta);
    response["error"] = json!({
        "kind": kind,
        "message": message,
        "details": {},
    });
    response
}

fn envelope_base(status: &str, meta: Value) -> Value {
    json!({
        "status": status,
        "meta": meta,
    })
}

fn request_meta(request: &ClientRequest) -> Value {
    response_meta(&request.op, &request.request_id)
}

fn bare_meta() -> Value {
    response_meta("", "")
}

fn response_meta(op: &str, request_id: &str) -> Value {
    json!({
        "envelope_version": ENVELOPE_VERSION,
        "op": op,
        "request_id": request_id,
        "duration_ms": 0.0,
        "resource_summary": {"fields": {}},
        "warnings": [],
    })
}

pub(crate) fn response_line(response: &Value) -> Vec<u8> {
    let mut line = serde_json::to_vec(response).unwrap_or_default();
    line.push(b'\n');
    line
}
