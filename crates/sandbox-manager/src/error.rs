use thiserror::Error;

use crate::model::{SandboxId, SandboxState};

#[derive(Debug, Error)]
pub enum ManagerError {
    #[error("invalid sandbox id: {value}")]
    InvalidSandboxId { value: String },

    #[error("invalid workspace root: {value}")]
    InvalidWorkspaceRoot { value: String },

    #[error("invalid image: {value}")]
    InvalidImage { value: String },

    #[error("invalid sandbox count: {value}")]
    InvalidSandboxCount { value: u64 },

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

    #[error("workspace setup failed: {message}")]
    WorkspaceSetupFailed { message: String },

    #[error("sandbox daemon forwarding failed: {message}")]
    ForwardingFailed { message: String },

    #[error("invalid export destination {value}: {reason}")]
    InvalidExportDest { value: String, reason: String },

    #[error("export failed: {message}")]
    ExportFailed { message: String },

    #[error("sandbox store lock poisoned")]
    StorePoisoned,

    #[error("sandbox registry persistence failed: {message}")]
    RegistryPersistFailed { message: String },
}

impl ManagerError {
    #[must_use]
    pub const fn protocol_kind(&self) -> &'static str {
        match self {
            Self::InvalidSandboxId { .. }
            | Self::InvalidWorkspaceRoot { .. }
            | Self::InvalidImage { .. }
            | Self::InvalidSandboxCount { .. }
            | Self::DuplicateSandbox { .. }
            | Self::MissingSandbox { .. }
            | Self::InvalidStateTransition { .. }
            | Self::InvalidExportDest { .. }
            | Self::DaemonUnavailable { .. } => sandbox_protocol::error_kind::INVALID_REQUEST,
            Self::RuntimeFailed { .. }
            | Self::DaemonInstallFailed { .. }
            | Self::ForwardingFailed { .. }
            | Self::StorePoisoned
            | Self::RegistryPersistFailed { .. } => sandbox_protocol::error_kind::INTERNAL_ERROR,
            Self::WorkspaceSetupFailed { .. } | Self::ExportFailed { .. } => {
                sandbox_protocol::error_kind::OPERATION_FAILED
            }
        }
    }

    #[must_use]
    pub fn into_response(self) -> sandbox_protocol::Response {
        sandbox_protocol::Response::fault(self.protocol_kind(), self.to_string())
    }
}
