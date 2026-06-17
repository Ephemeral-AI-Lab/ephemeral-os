//! The canonical error-`kind` vocabularies.
//!
//! Serialized `snake_case` on the wire. The daemon emits
//! [`ProtocolErrorKind`] variants; the gateway reuses the request-validation
//! subset at its pre-forward gate. Host/gateway forwarding failures use
//! [`HostGatewayErrorKind`]. Keeping both vocabularies here prevents string
//! drift across crates.

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
    /// The listener accepted the connection but is at its concurrency limit.
    ServerBusy,
    /// TCP only: configured auth token did not match.
    Unauthorized,
    /// `op` not registered in the daemon op table.
    UnknownOp,
    /// A handler raised; `details.error_id` carries a uuid4 hex.
    InternalError,
    /// Operation/gate policy refusal.
    Forbidden,
    /// Refused because an isolated network is active for this agent.
    ForbiddenInIsolatedNetwork,
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
            Self::ServerBusy => "server_busy",
            Self::Unauthorized => "unauthorized",
            Self::UnknownOp => "unknown_op",
            Self::InternalError => "internal_error",
            Self::Forbidden => "forbidden",
            Self::ForbiddenInIsolatedNetwork => "forbidden_in_isolated_network",
            Self::LifecycleInProgress => "lifecycle_in_progress",
        }
    }
}

/// Verified host/gateway forwarding error `kind` values.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum HostGatewayErrorKind {
    /// Host trace/audit persistence was unavailable before a response could be
    /// made durable.
    TraceUnavailable,
    /// A host-owned operation failed outside a request-shape problem.
    HostOperationFailed,
    /// The requested sandbox id is not in the host registry.
    UnknownSandbox,
    /// The sandbox container or daemon could not be reached or recovered.
    SandboxUnavailable,
    /// A mutating request may have been delivered before transport failed.
    UncertainOutcome,
}

impl HostGatewayErrorKind {
    /// The canonical `snake_case` wire string for this kind.
    #[must_use]
    pub const fn as_str(&self) -> &'static str {
        match self {
            Self::TraceUnavailable => "trace_unavailable",
            Self::HostOperationFailed => "host_operation_failed",
            Self::UnknownSandbox => "unknown_sandbox",
            Self::SandboxUnavailable => "sandbox_unavailable",
            Self::UncertainOutcome => "uncertain_outcome",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{HostGatewayErrorKind, ProtocolErrorKind};

    #[test]
    fn protocol_error_kind_wire_names_are_stable() {
        assert_eq!(ProtocolErrorKind::ServerBusy.as_str(), "server_busy");
        assert_eq!(
            ProtocolErrorKind::RequestTooLarge.as_str(),
            "request_too_large"
        );
    }

    #[test]
    fn host_gateway_error_kind_wire_names_are_stable() {
        assert_eq!(
            HostGatewayErrorKind::TraceUnavailable.as_str(),
            "trace_unavailable"
        );
        assert_eq!(
            HostGatewayErrorKind::UncertainOutcome.as_str(),
            "uncertain_outcome"
        );
    }
}
