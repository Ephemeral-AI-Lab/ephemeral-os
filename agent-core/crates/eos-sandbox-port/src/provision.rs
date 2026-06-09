//! Request-scoped sandbox provisioning port.
//!
//! This is the daemon-agnostic binding contract `eos-agent-core` depends on to
//! resolve the sandbox a request runs in. The concrete provisioner
//! (`RequestSandboxProvisioner`, Docker/daemon-backed) lives in
//! `eos-sandbox-host`; it implements this trait and maps its host error into the
//! port-level [`SandboxProvisionError`]. Keeping the trait and its binding type
//! here lets agent-core compose against the port without importing the host.

use async_trait::async_trait;
use eos_types::{RequestId, SandboxId};

/// Error raised while resolving a request's sandbox binding.
///
/// The port deliberately carries an opaque message rather than mirroring the
/// host's typed failure enum: agent-core only propagates this error (via
/// `anyhow` context), so a single message-bearing shape keeps the port free of
/// Docker/daemon vocabulary. The host implementor maps its own error into this
/// at the trait boundary.
#[derive(Debug, Clone, thiserror::Error)]
#[error("sandbox provisioning failed: {message}")]
pub struct SandboxProvisionError {
    /// User-facing description of the provisioning failure.
    pub message: String,
}

impl SandboxProvisionError {
    /// Build a [`SandboxProvisionError`] from a failure message.
    #[must_use]
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

/// The resolved sandbox↔request binding produced by a [`RequestProvisioner`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestSandboxBinding {
    /// The sandbox the request runs in.
    pub sandbox_id: SandboxId,
    /// The originating request.
    pub request_id: RequestId,
}

/// Request-scoped sandbox provisioning contract.
///
/// Runtime composition depends on this port: callers either provide an explicit
/// sandbox id to start, or ask the implementor to create and bind a fresh
/// request sandbox. Stored as `Arc<dyn RequestProvisioner>` at the composition
/// root, so it stays object-safe and uses `#[async_trait]`.
#[async_trait]
pub trait RequestProvisioner: Send + Sync + std::fmt::Debug {
    /// Resolve the sandbox binding for one request.
    ///
    /// # Errors
    /// Returns [`SandboxProvisionError`] if the implementor cannot start the
    /// explicit sandbox or create a fresh one.
    async fn prepare_for_run(
        &self,
        request_id: &RequestId,
        sandbox_id: Option<&str>,
    ) -> Result<RequestSandboxBinding, SandboxProvisionError>;
}
