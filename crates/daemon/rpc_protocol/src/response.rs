use serde_json::{json, Value};

use crate::request::Request;

#[derive(Debug, Clone)]
pub struct Response {
    value: Value,
}

impl Response {
    #[must_use]
    pub fn ok(_request: &Request<'_>, result: Value) -> Self {
        Self { value: result }
    }

    #[must_use]
    pub fn running(_request: &Request<'_>, result: Value) -> Self {
        Self { value: result }
    }

    #[must_use]
    pub fn service_error(_request: &Request<'_>, error: impl std::fmt::Display) -> Self {
        Self::fault("operation_failed", error.to_string())
    }

    #[must_use]
    pub fn unknown_op(request: &Request<'_>) -> Self {
        Self::fault("unknown_op", format!("unknown op: {}", request.name))
    }

    #[must_use]
    pub fn fault(kind: &'static str, message: impl Into<String>) -> Self {
        Self {
            value: json!({
                "error": {
                    "kind": kind,
                    "message": message.into(),
                    "details": {},
                },
            }),
        }
    }

    #[must_use]
    pub fn into_json_value(self) -> Value {
        self.value
    }
}

impl From<Response> for Value {
    fn from(response: Response) -> Self {
        response.into_json_value()
    }
}

#[must_use]
pub fn response_meta(op: &str, request_id: &str) -> Value {
    json!({
        "op": op,
        "request_id": request_id,
        "duration_ms": 0.0,
        "resource_summary": {"fields": {}},
        "warnings": [],
    })
}

#[must_use]
pub fn error_response(kind: &str, message: impl Into<String>) -> Value {
    error_response_with_meta(kind, message, json!({}), response_meta("", ""))
}

#[must_use]
pub fn error_response_with_details(
    kind: &str,
    message: impl Into<String>,
    details: Value,
) -> Value {
    error_response_with_meta(kind, message, details, response_meta("", ""))
}

#[must_use]
pub fn error_response_with_meta(
    kind: &str,
    message: impl Into<String>,
    details: Value,
    meta: Value,
) -> Value {
    json!({
        "status": "error",
        "error": {
            "kind": kind,
            "message": message.into(),
            "details": details,
        },
        "meta": meta,
    })
}

#[must_use]
pub fn ok_response(op: &str, request_id: &str, result: Value) -> Value {
    let mut response = response_base("ok", response_meta(op, request_id));
    response["result"] = result;
    response
}

#[must_use]
pub fn response_line(response: &Value) -> Vec<u8> {
    crate::framing::encode_json_line(response)
}

#[must_use]
pub fn response_status(response: &Value) -> &str {
    response
        .get("status")
        .and_then(Value::as_str)
        .filter(|status| valid_response_status(status))
        .unwrap_or("error")
}

#[must_use]
pub fn response_result_status(response: &Value) -> Option<&str> {
    response
        .get("result")
        .and_then(|result| result.get("status"))
        .and_then(Value::as_str)
}

#[must_use]
pub fn response_fault_kind(response: &Value) -> Option<&str> {
    response
        .get("error")
        .and_then(|error| error.get("kind"))
        .and_then(Value::as_str)
        .or_else(|| {
            (response.get("status").and_then(Value::as_str).is_none()).then_some("missing_status")
        })
}

#[must_use]
pub fn response_is_accepted(response: &Value) -> bool {
    matches!(response_status(response), "ok" | "running")
}

fn response_base(status: &str, meta: Value) -> Value {
    json!({
        "status": status,
        "meta": meta,
    })
}

fn valid_response_status(status: &str) -> bool {
    matches!(
        status,
        "ok" | "running" | "rejected" | "cancelled" | "timed_out" | "error"
    )
}
