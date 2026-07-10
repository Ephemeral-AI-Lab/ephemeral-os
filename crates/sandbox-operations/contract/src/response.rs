use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::error::{OperationError, OPERATION_FAILED};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct OperationResponse {
    value: Value,
}

impl OperationResponse {
    #[must_use]
    pub fn ok(result: Value) -> Self {
        Self { value: result }
    }

    #[must_use]
    pub fn from_json_value(value: Value) -> Self {
        Self { value }
    }

    #[must_use]
    pub fn running(result: Value) -> Self {
        Self { value: result }
    }

    #[must_use]
    pub fn service_error(error: impl std::fmt::Display) -> Self {
        Self::fault(OPERATION_FAILED, error.to_string())
    }

    #[must_use]
    pub fn unknown_op() -> Self {
        Self::fault("unknown_op", "unknown operation")
    }

    #[must_use]
    pub fn fault(kind: impl Into<String>, message: impl Into<String>) -> Self {
        Self::fault_with_details(kind, message, json!({}))
    }

    #[must_use]
    pub fn fault_with_details(
        kind: impl Into<String>,
        message: impl Into<String>,
        details: Value,
    ) -> Self {
        Self {
            value: OperationError::new(kind, message, details).into_json_value(),
        }
    }

    #[must_use]
    pub fn into_json_value(self) -> Value {
        self.value
    }

    #[must_use]
    pub fn as_json_value(&self) -> &Value {
        &self.value
    }
}

impl From<OperationResponse> for Value {
    fn from(response: OperationResponse) -> Self {
        response.into_json_value()
    }
}
