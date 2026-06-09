//! `AgentRun` DTO — one agent execution for one task (Rust `db/models/agent_run.py`).

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{AgentRunId, JsonObject, TaskId, UtcDateTime};

/// Immutable view of a persisted agent-run row (Rust `AgentRunRecord`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct AgentRun {
    /// Agent-run identifier.
    pub id: AgentRunId,
    /// The task this run executes (1:1, unique).
    pub task_id: Option<TaskId>,
    /// Bound agent profile name.
    pub agent_name: String,
    /// Terminal payload snapshot, written at finish.
    pub terminal_payload: Option<JsonObject>,
    /// Tokens consumed by the run.
    pub token_count: i64,
    /// Error message, if the run failed.
    pub error: Option<String>,
    /// Creation timestamp.
    pub created_at: UtcDateTime,
    /// Finish timestamp, if finished.
    pub finished_at: Option<UtcDateTime>,
}
