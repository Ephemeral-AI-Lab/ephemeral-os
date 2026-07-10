use serde_json::{json, Value};

pub const INTERNAL_ERROR: &str = "internal_error";
pub const INVALID_REQUEST: &str = "invalid_request";
pub const OPERATION_FAILED: &str = "operation_failed";

#[derive(Debug, Clone, PartialEq)]
pub struct OperationError {
    kind: String,
    message: String,
    details: Value,
}

impl OperationError {
    #[must_use]
    pub fn new(kind: impl Into<String>, message: impl Into<String>, details: Value) -> Self {
        Self {
            kind: kind.into(),
            message: message.into(),
            details,
        }
    }

    #[must_use]
    pub fn kind(&self) -> &str {
        &self.kind
    }

    #[must_use]
    pub fn message(&self) -> &str {
        &self.message
    }

    #[must_use]
    pub fn details(&self) -> &Value {
        &self.details
    }

    #[must_use]
    pub fn into_json_value(self) -> Value {
        json!({
            "error": {
                "kind": self.kind,
                "message": self.message,
                "details": self.details,
            }
        })
    }
}

#[must_use]
pub fn error_response_with_details(
    kind: impl Into<String>,
    message: impl Into<String>,
    details: Value,
) -> Value {
    OperationError::new(kind, message, details).into_json_value()
}
