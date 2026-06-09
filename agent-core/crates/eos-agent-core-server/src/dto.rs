//! Public request/response DTOs for [`AgentCoreService`](crate::AgentCoreService).

use eos_types::{RequestId, RequestStatus, SandboxId, UtcDateTime};

/// Input for creating a top-level user request.
#[derive(Debug, Clone)]
pub struct CreateUserRequestInput {
    /// Root prompt sent to the root agent.
    pub prompt: String,
    /// Optional existing sandbox to bind; `None` provisions a fresh sandbox.
    pub sandbox_id: Option<SandboxId>,
}

/// Output from creating a user request.
#[derive(Debug, Clone)]
pub struct CreateUserRequestOutput {
    /// Minted request id.
    pub request_id: RequestId,
}

/// Input for cancelling a top-level user request.
#[derive(Debug, Clone)]
pub struct CancelUserRequestInput {
    /// Request to cancel.
    pub request_id: RequestId,
    /// Cancellation reason propagated to active agent runs.
    pub reason: String,
}

/// Output from cancelling a user request.
#[derive(Debug, Clone)]
pub struct CancelUserRequestOutput {
    /// Cancelled request id.
    pub request_id: RequestId,
    /// Number of agent runs signalled through `AgentRunService`.
    pub cancelled_agent_run_count: usize,
}

/// Summary row for listing user requests.
#[derive(Debug, Clone)]
pub struct UserRequestSummary {
    /// Request id.
    pub request_id: RequestId,
    /// Request lifecycle status.
    pub status: RequestStatus,
    /// Bound sandbox id.
    pub sandbox_id: Option<SandboxId>,
    /// Creation timestamp.
    pub created_at: UtcDateTime,
    /// Finish timestamp, if terminal.
    pub finished_at: Option<UtcDateTime>,
}

/// Detail row for reading one user request.
#[derive(Debug, Clone)]
pub struct UserRequestDetail {
    /// Request id.
    pub request_id: RequestId,
    /// Request lifecycle status.
    pub status: RequestStatus,
    /// Bound sandbox id.
    pub sandbox_id: Option<SandboxId>,
    /// Original request prompt.
    pub prompt: String,
    /// Creation timestamp.
    pub created_at: UtcDateTime,
    /// Last update timestamp.
    pub updated_at: UtcDateTime,
    /// Finish timestamp, if terminal.
    pub finished_at: Option<UtcDateTime>,
}
