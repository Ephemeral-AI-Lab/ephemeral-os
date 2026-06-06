//! The backend domain error surfaced when serving DTOs.

use thiserror::Error;

/// Errors the backend API/runtime surfaces to callers. Kept `#[non_exhaustive]`
/// because later phases add transport- and stream-specific variants.
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum BackendError {
    /// A requested resource does not exist.
    #[error("{resource} {id} not found")]
    NotFound {
        /// Resource kind, e.g. `"user-request"` or `"sandbox"`.
        resource: &'static str,
        /// The missing resource id.
        id: String,
    },
    /// The request payload was malformed or violated a v1 constraint.
    #[error("invalid request: {0}")]
    BadRequest(String),
}
