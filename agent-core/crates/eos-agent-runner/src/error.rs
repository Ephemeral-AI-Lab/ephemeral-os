//! Agent-run lifecycle errors.

use eos_types::AgentRunId;

/// Error returned by the agent-run lifecycle API.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum AgentRunError {
    /// The requested agent run is not active in this process and has no durable
    /// terminal outcome available.
    #[error("agent run {0} is not active in this process")]
    NotActiveInProcess(AgentRunId),

    /// The requested agent name was not registered.
    #[error("agent {0:?} is not registered")]
    AgentNotRegistered(String),

    /// The requested agent exists but is not launchable for this operation.
    #[error("agent {agent_name:?} is not a {expected} agent (actual: {actual})")]
    WrongAgentType {
        /// Requested agent name.
        agent_name: String,
        /// Expected type label.
        expected: &'static str,
        /// Actual type label.
        actual: &'static str,
    },

    /// Recursive subagent launch is disallowed.
    #[error("subagents may not spawn further subagents")]
    RecursiveSubagent,

    /// Waiting failed because the completion channel closed.
    #[error("agent run completion channel closed for {0}")]
    CompletionChannelClosed(AgentRunId),

    /// A store, engine, or framework operation failed.
    #[error("agent run failed: {0}")]
    Internal(String),
}
