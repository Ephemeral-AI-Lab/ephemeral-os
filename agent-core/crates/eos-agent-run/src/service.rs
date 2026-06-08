//! Agent-run lifecycle trait.

use async_trait::async_trait;
use eos_types::AgentRunId;

use crate::{AgentRunError, AgentRunOutcome, SpawnAgentRequest};

/// Lifecycle API for spawning, waiting, polling, and cancelling agent runs.
#[async_trait]
pub trait AgentRunApi: Send + Sync {
    /// Spawn an agent and return its natural run id immediately.
    async fn spawn_agent(
        &self,
        request: SpawnAgentRequest,
    ) -> Result<AgentRunId, AgentRunError>;

    /// Wait for one agent run to publish a terminal outcome.
    async fn wait_for_agent_outcomes(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<AgentRunOutcome, AgentRunError>;

    /// Nonblocking terminal outcome poll for background managers.
    async fn poll_agent_run_outcome(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<Option<AgentRunOutcome>, AgentRunError>;

    /// Cancel one active agent run.
    async fn cancel_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        reason: &str,
    ) -> Result<(), AgentRunError>;
}
