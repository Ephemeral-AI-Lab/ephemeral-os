//! Error type for backend-facing agent-core request operations.

use eos_types::{AgentRunError, CoreError, RequestId, RequestStatus};

/// Errors returned by [`AgentCoreService`](crate::AgentCoreService).
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum AgentCoreServerError {
    /// The requested user request does not exist.
    #[error("user request {0} was not found")]
    UserRequestNotFound(RequestId),

    /// The requested user request is already terminal.
    #[error("user request {request_id} already finished with status {status:?}")]
    UserRequestAlreadyFinished {
        /// Request id.
        request_id: RequestId,
        /// Current terminal status.
        status: RequestStatus,
    },

    /// Sandbox provisioning failed.
    #[error("sandbox provisioning failed: {0}")]
    SandboxProvision(String),

    /// Agent-run lifecycle failed.
    #[error("agent run failed: {0}")]
    AgentRun(#[from] AgentRunError),

    /// Store operation failed.
    #[error("store failed: {0}")]
    Store(#[from] CoreError),
}
