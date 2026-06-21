use sandbox_protocol::error_kind;
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
}

impl GatewayError {
    #[must_use]
    pub const fn response_kind(&self) -> &'static str {
        match self {
            Self::Json(_) => error_kind::BAD_JSON,
            Self::BadRequest { kind, .. } => kind,
            Self::RequestTooLarge { .. } => error_kind::REQUEST_TOO_LARGE,
            Self::MissingNewline => error_kind::INVALID_REQUEST,
            Self::Io(_) => error_kind::INTERNAL_ERROR,
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
    pub fn to_response_value(&self) -> Value {
        error_response(
            self.response_kind(),
            self.to_string(),
            self.response_details(),
        )
    }
}

#[must_use]
pub(crate) fn error_response(kind: &str, message: impl Into<String>, details: Value) -> Value {
    sandbox_protocol::error_response_with_details(kind, message, details)
}
