use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use super::OpError;

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
            Self::Success(value) => value,
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
    json!({
        "success": false,
        "error": {
            "kind": error.kind,
            "message": error.message,
            "details": error.details.unwrap_or_else(|| json!({})),
        },
    })
}

fn error_response(error: OpResponseError) -> Value {
    let is_internal_error = error.kind == OpResponseErrorKind::InternalError;
    let kind = serde_json::to_value(error.kind).unwrap_or(Value::Null);
    json!({
        "success": false,
        "warnings": [],
        "timings": {},
        "error": {
            "kind": kind,
            "message": error.message,
            "details": error_details(is_internal_error, error.details),
        },
    })
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
