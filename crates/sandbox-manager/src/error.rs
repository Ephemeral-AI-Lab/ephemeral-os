use thiserror::Error;

use crate::model::{SandboxId, SandboxState};

pub type ManagerResult<T> = Result<T, ManagerError>;

#[derive(Debug, Error)]
pub enum ManagerError {
    #[error("invalid sandbox id: {value}")]
    InvalidSandboxId { value: String },

    #[error("sandbox already exists: {id}")]
    DuplicateSandbox { id: SandboxId },

    #[error("sandbox not found: {id}")]
    MissingSandbox { id: SandboxId },

    #[error("invalid state transition for {id}: {from} -> {to}")]
    InvalidStateTransition {
        id: SandboxId,
        from: SandboxState,
        to: SandboxState,
    },

    #[error("sandbox daemon unavailable for {id}")]
    DaemonUnavailable { id: SandboxId },

    #[error("sandbox runtime failed: {message}")]
    RuntimeFailed { message: String },

    #[error("sandbox daemon install failed: {message}")]
    DaemonInstallFailed { message: String },

    #[error("sandbox daemon forwarding failed: {message}")]
    ForwardingFailed { message: String },

    #[error("sandbox store lock poisoned")]
    StorePoisoned,
}

impl ManagerError {
    #[must_use]
    pub const fn protocol_kind(&self) -> &'static str {
        match self {
            Self::InvalidSandboxId { .. }
            | Self::DuplicateSandbox { .. }
            | Self::MissingSandbox { .. }
            | Self::InvalidStateTransition { .. }
            | Self::DaemonUnavailable { .. } => sandbox_protocol::error_kind::INVALID_REQUEST,
            Self::RuntimeFailed { .. }
            | Self::DaemonInstallFailed { .. }
            | Self::ForwardingFailed { .. }
            | Self::StorePoisoned => sandbox_protocol::error_kind::INTERNAL_ERROR,
        }
    }

    #[must_use]
    pub fn into_response(self) -> sandbox_protocol::Response {
        sandbox_protocol::Response::fault(self.protocol_kind(), self.to_string())
    }
}
