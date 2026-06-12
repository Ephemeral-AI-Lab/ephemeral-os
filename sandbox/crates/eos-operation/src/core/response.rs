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
        op_fault(error.kind, error.message, error.details.unwrap_or_else(|| json!({}))),
        ResponseMeta::default(),
    ))
}

fn error_response(error: OpResponseError) -> Value {
    let is_internal_error = error.kind == OpResponseErrorKind::InternalError;
    let kind = serde_json::to_value(error.kind)
        .ok()
        .and_then(|value| value.as_str().map(str::to_owned))
        .unwrap_or_else(|| "internal_error".to_owned());
    let details = error_details(is_internal_error, error.details);
    let fault = if is_internal_error {
        OperationFault::internal(error.message, fault_details(details))
    } else {
        op_fault(kind, error.message, details)
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
    if details.is_null() {
        FaultDetails::default()
    } else {
        FaultDetails::default().with_field("details", details)
    }
}

fn error_details(is_internal_error: bool, details: Value) -> Value {
    if !is_internal_error {
        return if details.is_null() {
            json!({})
        } else {
            details
        };
    }
    let mut details = match details {
        Value::Null => serde_json::Map::new(),
        Value::Object(details) => details,
        other => {
            let mut object = serde_json::Map::new();
            object.insert("value".to_owned(), other);
            object
        }
    };
    details
        .entry("error_id")
        .or_insert_with(|| Value::String(uuid::Uuid::new_v4().simple().to_string()));
    Value::Object(details)
}
