//! Sandbox daemon error algebra and response-kind mapping.

use sandbox_operation_contract::error::INTERNAL_ERROR;
use sandbox_protocol::error::{REQUEST_TOO_LARGE, UNAUTHORIZED};
use thiserror::Error;

#[derive(Debug, Error)]
#[non_exhaustive]
pub enum SandboxDaemonError {
    #[error("daemon io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("request exceeds {limit} byte limit")]
    RequestTooLarge { limit: usize },

    #[error("daemon request authentication failed")]
    Unauthorized,
}

impl SandboxDaemonError {
    /// Map this error onto the JSON error `kind`.
    #[must_use]
    pub const fn response_kind(&self) -> &'static str {
        match self {
            Self::RequestTooLarge { .. } => REQUEST_TOO_LARGE,
            Self::Unauthorized => UNAUTHORIZED,
            _ => INTERNAL_ERROR,
        }
    }
}
