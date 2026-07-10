use sandbox_operation_contract::{error as operation_error, OperationResponse};
use sandbox_protocol::error::{BAD_JSON, REQUEST_TOO_LARGE, UNAUTHORIZED};
use serde_json::{json, Value};
use thiserror::Error;

#[derive(Debug, Error)]
#[non_exhaustive]
pub enum GatewayError {
    #[error("gateway io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("bad json: {0}")]
    Json(#[from] serde_json::Error),

    #[error("{message}")]
    BadRequest { kind: &'static str, message: String },

    #[error("request exceeds {limit} byte limit")]
    RequestTooLarge { limit: usize },

    #[error("gateway request was not newline terminated")]
    MissingNewline,

    #[error("gateway authentication token is missing or invalid")]
    Unauthorized,
}

impl GatewayError {
    #[must_use]
    pub const fn response_kind(&self) -> &'static str {
        match self {
            Self::Json(_) => BAD_JSON,
            Self::BadRequest { kind, .. } => kind,
            Self::RequestTooLarge { .. } => REQUEST_TOO_LARGE,
            Self::MissingNewline => operation_error::INVALID_REQUEST,
            Self::Unauthorized => UNAUTHORIZED,
            Self::Io(_) => operation_error::INTERNAL_ERROR,
        }
    }

    #[must_use]
    pub fn response_details(&self) -> Value {
        match self {
            Self::RequestTooLarge { limit } => json!({ "limit": limit }),
            _ => json!({}),
        }
    }

    #[must_use]
    pub fn to_response(&self) -> OperationResponse {
        error_response(
            self.response_kind(),
            self.to_string(),
            self.response_details(),
        )
    }
}

#[must_use]
pub(crate) fn error_response(
    kind: impl Into<String>,
    message: impl Into<String>,
    details: Value,
) -> OperationResponse {
    OperationResponse::fault_with_details(kind, message, details)
}
