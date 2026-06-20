//! Daemon error algebra and response-kind mapping.

use thiserror::Error;

#[derive(Debug, Error)]
#[non_exhaustive]
pub enum DaemonError {
    #[error("daemon io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("request exceeds {limit} byte limit")]
    RequestTooLarge { limit: usize },

    #[error("daemon request authentication failed")]
    Unauthorized,

    #[error("forbidden: {0}")]
    Forbidden(String),
}

impl DaemonError {
    /// Map this error onto the JSON error `kind`.
    #[must_use]
    pub const fn response_kind(&self) -> &'static str {
        match self {
            Self::RequestTooLarge { .. } => "request_too_large",
            Self::Unauthorized => "unauthorized",
            Self::Forbidden(_) => "forbidden",
            _ => "internal_error",
        }
    }
}
