//! Agent-loop DTOs.

use eos_llm_client::Message;
use eos_tool_ports::ToolResult;
use eos_types::AgentRunId;

/// Thin request to start one agent loop.
#[derive(Debug, Clone)]
pub struct StartAgentLoopRequest {
    /// Agent-run id.
    pub agent_run_id: AgentRunId,
    /// Runner-prepared initial messages.
    pub initial_messages: Vec<AgentLoopMessage>,
    /// Resolved model key.
    pub model_key: String,
    /// Completion token cap.
    pub max_completion_tokens: u32,
    /// Tool-call limit.
    pub tool_call_limit: u32,
}

/// Engine-local loop message wrapper.
#[derive(Debug, Clone)]
pub enum AgentLoopMessage {
    /// System prompt text.
    SystemPrompt(String),
    /// User message.
    UserMessage(Message),
    /// Assistant message.
    AssistantMessage(Message),
}

/// Terminal loop outcome envelope.
#[derive(Debug, Clone)]
pub struct AgentLoopOutcome {
    /// Outcome kind.
    pub kind: AgentLoopOutcomeKind,
    /// Final loop transcript.
    pub final_conversation_messages: Vec<AgentLoopMessage>,
    /// Total provider token count when known.
    pub total_token_count: Option<i64>,
}

/// Narrow loop outcome kinds for this migration.
#[derive(Debug, Clone)]
pub enum AgentLoopOutcomeKind {
    /// A terminal tool submitted successfully.
    TerminalToolSubmitted {
        /// Terminal tool result.
        outcome: ToolResult,
    },
    /// The loop failed or exited without a valid terminal submission.
    LoopFailed {
        /// Human-readable error summary.
        error_summary: String,
    },
}
