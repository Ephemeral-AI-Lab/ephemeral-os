//! Agent-run terminal outcome DTOs.

use eos_llm_client::Message;
use eos_tools::ToolResult;
use eos_types::{AgentRunId, JsonObject};

/// Terminal outcome for one agent run.
#[derive(Debug, Clone)]
pub struct AgentRunOutcome {
    /// Agent-run id.
    pub agent_run_id: AgentRunId,
    /// Terminal status.
    pub status: AgentRunStatus,
    /// Terminal model-facing tool result, when one was submitted.
    pub terminal_result: Option<ToolResult>,
    /// Persisted terminal payload, when available.
    pub terminal_payload: Option<JsonObject>,
    /// Final message history, when the runner makes it available.
    pub message_history: Vec<Message>,
    /// Provider token count, when known.
    pub token_count: Option<i64>,
    /// Framework/engine error summary, when the run failed.
    pub error: Option<String>,
}

/// Agent-run terminal status.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AgentRunStatus {
    /// The run completed normally.
    Completed,
    /// The run failed.
    Failed,
    /// The run was cancelled.
    Cancelled,
}
