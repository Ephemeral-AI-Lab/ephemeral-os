//! Engine-facing agent-run persistence contracts.

use async_trait::async_trait;

use crate::{AgentRun, AgentRunId, CoreError, JsonObject, TaskId};

use super::Sealed;

/// Persistence surface for [`AgentRun`].
#[async_trait]
pub trait AgentRunStore: Sealed + Send + Sync {
    /// Create a run row with only the create-time fields set.
    async fn create_run(
        &self,
        agent_run_id: &AgentRunId,
        task_id: Option<&TaskId>,
        agent_name: &str,
    ) -> Result<AgentRun, CoreError>;

    /// Write the finish-time fields. `Ok(None)` means the run does not exist.
    async fn finish_run(
        &self,
        agent_run_id: &AgentRunId,
        terminal_payload: Option<&JsonObject>,
        token_count: i64,
        error: Option<&str>,
    ) -> Result<Option<AgentRun>, CoreError>;

    /// Load a run by id.
    async fn get(&self, agent_run_id: &AgentRunId) -> Result<Option<AgentRun>, CoreError>;

    /// The latest agent run for one task, if any.
    async fn get_for_task(&self, task_id: &TaskId) -> Result<Option<AgentRun>, CoreError>;
}
