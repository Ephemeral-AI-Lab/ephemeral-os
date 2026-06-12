use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use super::{FaultDetails, OpError, OperationEnvelope, OperationFault, ResponseMeta};

#[derive(Debug, Clone, PartialEq)]
pub enum OpResponse {
    Success(Value),
    Refused(OpError),
    Error(OpResponseError),
}

impl OpResponse {
    #[must_use]
    pub fn into_wire(self) -> Value {
        match self {
            Self::Success(value) => ok_response(value),
            Self::Refused(error) => refused_response(error),
            Self::Error(error) => error_response(error),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct OpResponseError {
    pub kind: OpResponseErrorKind,
    pub message: String,
    pub details: Value,
}

impl OpResponseError {
    #[must_use]
    pub fn new(kind: OpResponseErrorKind, message: impl Into<String>, details: Value) -> Self {
        Self {
            kind,
            message: message.into(),
            details,
        }
    }

    #[must_use]
    pub fn invalid_request(message: impl Into<String>) -> Self {
        Self::new(OpResponseErrorKind::InvalidRequest, message, json!({}))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OpResponseErrorKind {
    InvalidRequest,
    BadJson,
    RequestTooLarge,
    Unauthorized,
    UnknownOp,
    InternalError,
    Forbidden,
    ForbiddenInIsolatedWorkspace,
    LifecycleInProgress,
}

fn refused_response(error: OpError) -> Value {
    envelope_value(OperationEnvelope::<Value>::rejected(
        op_fault(
            error.kind,
            error.message,
            error.details.unwrap_or_else(|| json!({})),
        ),
        ResponseMeta::default(),
    ))
}

fn error_response(error: OpResponseError) -> Value {
    let is_internal_error = error.kind == OpResponseErrorKind::InternalError;
    let kind = serde_json::to_value(error.kind)
        .ok()
        .and_then(|value| value.as_str().map(str::to_owned))
        .unwrap_or_else(|| "internal_error".to_owned());
    let fault = if is_internal_error {
        OperationFault::internal(error.message, fault_details(error.details))
    } else {
        op_fault(kind, error.message, error.details)
    };
    envelope_value(OperationEnvelope::<Value>::error(
        fault,
        ResponseMeta::default(),
    ))
}

fn ok_response(value: Value) -> Value {
    if is_operation_envelope(&value) {
        return value;
    }
    envelope_value(OperationEnvelope::ok(value, ResponseMeta::default()))
}

fn envelope_value<T: serde::Serialize>(envelope: OperationEnvelope<T>) -> Value {
    serde_json::to_value(envelope).expect("operation envelope serializes")
}

fn is_operation_envelope(value: &Value) -> bool {
    let Some(object) = value.as_object() else {
        return false;
    };
    let Some("ok" | "running" | "rejected" | "cancelled" | "timed_out" | "error") =
        object.get("status").and_then(Value::as_str)
    else {
        return false;
    };
    object.contains_key("meta") && (object.contains_key("result") || object.contains_key("error"))
}

fn op_fault(kind: impl Into<String>, message: impl Into<String>, details: Value) -> OperationFault {
    OperationFault::new(kind, message).with_details(fault_details(details))
}

fn fault_details(details: Value) -> FaultDetails {
    match details {
        Value::Null => FaultDetails::default(),
        Value::Object(fields) if fields.is_empty() => FaultDetails::default(),
        Value::Object(fields) => fields
            .into_iter()
            .fold(FaultDetails::default(), |details, (key, value)| {
                details.with_field(key, value)
            }),
        value => FaultDetails::default().with_field("value", value),
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{OpResponse, OpResponseError, OpResponseErrorKind};
    use crate::OpError;

    #[test]
    fn success_response_renders_ok_envelope_without_flattening_payload_status() {
        let response =
            OpResponse::Success(json!({"success": true, "status": "committed"})).into_wire();

        assert_eq!(response["status"], json!("ok"));
        assert_eq!(response["result"]["success"], json!(true));
        assert_eq!(response["result"]["status"], json!("committed"));
        assert!(response["meta"].is_object());
    }

    #[test]
    fn refused_response_preserves_structured_detail_fields() {
        let response = OpResponse::Refused(OpError {
            kind: "invalid_argument",
            message: "caller_id is required".to_owned(),
            details: Some(json!({"key": "caller_id"})),
        })
        .into_wire();

        assert_eq!(response["status"], json!("rejected"));
        assert_eq!(response["error"]["kind"], json!("invalid_argument"));
        assert_eq!(
            response["error"]["details"]["fields"],
            json!({"key": "caller_id"})
        );
    }

    #[test]
    fn internal_error_response_uses_explicit_error_id_and_detail_fields() {
        let response = OpResponse::Error(OpResponseError::new(
            OpResponseErrorKind::InternalError,
            "daemon invocation failed",
            json!({"op": "api.test.failure"}),
        ))
        .into_wire();

        assert_eq!(response["status"], json!("error"));
        assert_eq!(response["error"]["kind"], json!("internal_error"));
        assert_eq!(
            response["error"]["details"]["fields"]["op"],
            json!("api.test.failure")
        );
        assert_eq!(
            response["error"]["error_id"].as_str().map(str::len),
            Some(32)
        );
    }
}
