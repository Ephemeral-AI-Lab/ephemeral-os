//! Agent-core cancellation contracts.

use async_trait::async_trait;

use crate::{AgentRunId, CoreError, TaskId};

/// Error returned by recursive request/workflow cancellation.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum CancelError {
    /// An upstream store operation failed.
    #[error("store error: {0}")]
    Store(#[from] CoreError),
    /// A lifecycle invariant broke or an internal operation failed.
    #[error("{0}")]
    Internal(String),
}

/// Recursive agent-core cancellation primitives.
#[async_trait]
pub trait AgentCoreCancellationApi: Send + Sync {
    /// Cancel a persisted task and any live run bound to it.
    async fn cancel_task(&self, task_id: &TaskId, reason: &str) -> Result<(), CancelError>;

    /// Cancel a live agent run.
    async fn cancel_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        reason: &str,
    ) -> Result<(), CancelError>;
}
