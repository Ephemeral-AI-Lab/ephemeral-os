//! `AgentRun` DTO — one agent execution for one task (Rust `db/models/agent_run.py`).

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{AgentRunId, JsonObject, TaskId, UtcDateTime};

/// Immutable view of a persisted agent-run row (Rust `AgentRunRecord`).
///
/// `initial_messages`/`message_history` stay as provider-neutral `JsonObject`
/// blocks; typed `Message` modeling is owned by `eos-llm-client` (lifting it
/// here would invert the dependency DAG).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct AgentRun {
    /// Agent-run identifier.
    pub id: AgentRunId,
    /// The task this run executes (1:1, unique).
    pub task_id: Option<TaskId>,
    /// Transcript seed set at `create_run`; null-preserving.
    pub initial_messages: Option<Vec<JsonObject>>,
    /// Bound agent profile name.
    pub agent_name: String,
    /// Final transcript, written at `finish_run`; `None` until then.
    pub message_history: Option<Vec<JsonObject>>,
    /// Flattened terminal tool result, written at `finish_run`.
    pub terminal_tool_result: Option<JsonObject>,
    /// Tokens consumed by the run.
    pub token_count: i64,
    /// Error message, if the run failed.
    pub error: Option<String>,
    /// Creation timestamp.
    pub created_at: UtcDateTime,
    /// Finish timestamp, if finished.
    pub finished_at: Option<UtcDateTime>,
}
