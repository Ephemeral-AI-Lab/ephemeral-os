//! The canonical daemon error-`kind` vocabulary.
//!
//! Serialized `snake_case` on the wire. The daemon emits all variants; the
//! gateway reuses the request-validation subset at its pre-forward gate. Both
//! sides share this one enum so the strings cannot drift.

use serde::{Deserialize, Serialize};

/// Verified daemon error `kind` values. Serialized `snake_case` on the wire.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum ProtocolErrorKind {
    /// `op` missing/non-string/empty, or `args` present but not a dict.
    InvalidRequest,
    /// Request line was not valid UTF-8 JSON.
    BadJson,
    /// Request line exceeded `MAX_REQUEST_BYTES`.
    RequestTooLarge,
    /// TCP only: configured auth token did not match.
    Unauthorized,
    /// `op` not registered in the daemon op table.
    UnknownOp,
    /// A handler raised; `details.error_id` carries a uuid4 hex.
    InternalError,
    /// Operation/gate policy refusal.
    Forbidden,
    /// Refused because an isolated workspace is active for this agent.
    ForbiddenInIsolatedWorkspace,
    /// Refused because a lifecycle operation is in progress.
    LifecycleInProgress,
}

impl ProtocolErrorKind {
    /// The canonical `snake_case` wire string for this kind.
    #[must_use]
    pub const fn as_str(&self) -> &'static str {
        match self {
            Self::InvalidRequest => "invalid_request",
            Self::BadJson => "bad_json",
            Self::RequestTooLarge => "request_too_large",
            Self::Unauthorized => "unauthorized",
            Self::UnknownOp => "unknown_op",
            Self::InternalError => "internal_error",
            Self::Forbidden => "forbidden",
            Self::ForbiddenInIsolatedWorkspace => "forbidden_in_isolated_workspace",
            Self::LifecycleInProgress => "lifecycle_in_progress",
        }
    }
}
