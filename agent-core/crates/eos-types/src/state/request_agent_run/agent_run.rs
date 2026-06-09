//! Agent-run persistence DTOs with status and kind vocabularies.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{
    AgentName, AgentRunId, AgentType, JsonObject, RequestId, SubmissionOutcome, ToolUseId,
    UtcDateTime,
};

/// Lifecycle status of a persisted agent run.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionStatus {
    /// Created, not yet started.
    Pending,
    /// Currently executing.
    Running,
    /// Completed successfully.
    Done,
    /// Completed with failure.
    Failed,
    /// Could not proceed (blocked on an unmet dependency).
    Blocked,
    /// Cancelled before reaching a natural terminal. Blocks DAG descendants the
    /// same way `Failed` does.
    Cancelled,
}

impl ExecutionStatus {
    /// Whether this is a terminal execution status.
    #[must_use]
    pub const fn is_terminal(self) -> bool {
        matches!(
            self,
            Self::Done | Self::Failed | Self::Blocked | Self::Cancelled
        )
    }
}

/// Persisted agent-run row.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct AgentRun {
    /// Agent-run execution and record identity.
    pub agent_run_id: AgentRunId,
    /// Owning request.
    pub request_id: RequestId,
    /// Runtime agent profile type.
    pub agent_type: AgentType,
    /// Lifecycle status.
    pub status: ExecutionStatus,
    /// Bound agent profile.
    pub agent_name: AgentName,
    /// Exact parent agent run that launched this run.
    #[serde(default)]
    pub parent_agent_run_id: Option<AgentRunId>,
    /// Model tool-use id that launched this run, if available.
    #[serde(default)]
    pub tool_use_id: Option<ToolUseId>,
    /// Raw terminal payload projection, if any.
    #[serde(default)]
    pub terminal_payload: Option<JsonObject>,
    /// Typed mirror of the terminal payload, if any.
    #[serde(default)]
    pub submission_outcome: Option<SubmissionOutcome>,
    /// Provider token count.
    pub token_count: i64,
    /// Terminal error summary, if any.
    #[serde(default)]
    pub error: Option<String>,
    /// Creation timestamp.
    pub created_at: UtcDateTime,
    /// Last-update timestamp.
    pub updated_at: UtcDateTime,
    /// Finish timestamp, if terminal.
    #[serde(default)]
    pub finished_at: Option<UtcDateTime>,
}

/// Running agent-run lineage row used for request-scoped cancellation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RunningRequestAgentRun {
    /// Owning request.
    pub request_id: RequestId,
    /// Agent-run execution identity.
    pub agent_run_id: AgentRunId,
    /// Current running status.
    pub status: ExecutionStatus,
}
