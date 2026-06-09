//! `Task` — the persisted agent interface — with its status and role vocabularies.
//!
//! Ports `task/task.py`. `Task.role` uses the local 4-variant [`TaskRole`] so
//! workflow lineage stays separate from profile launch classes.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{
    AgentName, AgentRunId, AttemptId, IterationId, JsonObject, ParentedAgentRunKind, RequestId,
    TaskId, ToolUseId, UtcDateTime, WorkflowId,
};

use crate::ExecutionTaskOutcome;

/// Lifecycle status of a persisted [`Task`] (Rust `TaskStatus`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum TaskStatus {
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

impl TaskStatus {
    /// Whether this is a terminal generator status
    /// (Rust `TERMINAL_GENERATOR_STATUSES`).
    #[must_use]
    pub const fn is_terminal_generator(self) -> bool {
        matches!(
            self,
            Self::Done | Self::Failed | Self::Blocked | Self::Cancelled
        )
    }
}

/// The four persisted task roles (Rust `TASK_AGENT_ROLES`). The execution
/// state role is `Generator`; no profile-alias role enters persisted state
/// (anchor §4, GC-state-02).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum TaskRole {
    /// Root request agent (`Task(role=root, workflow_id=None)`).
    Root,
    /// Planner agent authoring an attempt's generator/reducer DAG.
    Planner,
    /// Generator (execution) task.
    Generator,
    /// Reducer task — the attempt's exit gate.
    Reducer,
}

impl TaskRole {
    /// The canonical `snake_case` token.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Root => "root",
            Self::Planner => "planner",
            Self::Generator => "generator",
            Self::Reducer => "reducer",
        }
    }
}

/// The four persisted task roles, mirroring Rust `TASK_AGENT_ROLES`.
pub const TASK_AGENT_ROLES: [TaskRole; 4] = [
    TaskRole::Root,
    TaskRole::Planner,
    TaskRole::Generator,
    TaskRole::Reducer,
];

/// Immutable view of a persisted task (Rust `task/task.py:Task`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Task {
    /// Task identifier.
    pub id: TaskId,
    /// Owning request.
    pub request_id: RequestId,
    /// Agent role.
    pub role: TaskRole,
    /// Instruction text the agent runs against.
    pub instruction: String,
    /// Lifecycle status.
    pub status: TaskStatus,
    /// Owning workflow, if delegated (`None` for the root task).
    #[serde(default)]
    pub workflow_id: Option<WorkflowId>,
    /// Owning iteration, if any.
    #[serde(default)]
    pub iteration_id: Option<IterationId>,
    /// Owning attempt, if any.
    #[serde(default)]
    pub attempt_id: Option<AttemptId>,
    /// Bound agent profile name, if assigned.
    #[serde(default)]
    pub agent_name: Option<String>,
    /// Task ids this task depends on (the `needs` edges).
    #[serde(default)]
    pub needs: Vec<TaskId>,
    /// Recorded execution outcomes (pre-normalized at the `eos-db` boundary).
    #[serde(default)]
    pub outcomes: Vec<ExecutionTaskOutcome>,
    /// Flattened terminal tool result, if a terminal has stamped one.
    #[serde(default)]
    pub terminal_tool_result: Option<JsonObject>,
}

/// Merged persisted task-agent-run row.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct TaskRun {
    /// Schedulable task identity.
    pub task_id: TaskId,
    /// Agent-run execution and record identity.
    pub agent_run_id: AgentRunId,
    /// Owning request.
    pub request_id: RequestId,
    /// Workflow role for this task-agent-run.
    pub role: TaskRole,
    /// Lifecycle status.
    pub status: TaskStatus,
    /// Owning workflow for planner/generator/reducer rows.
    #[serde(default)]
    pub workflow_id: Option<WorkflowId>,
    /// Owning iteration for planner/generator/reducer rows.
    #[serde(default)]
    pub iteration_id: Option<IterationId>,
    /// Owning attempt for planner/generator/reducer rows.
    #[serde(default)]
    pub attempt_id: Option<AttemptId>,
    /// Bound agent profile.
    pub agent_name: AgentName,
    /// Terminal payload projection, if any.
    #[serde(default)]
    pub terminal_payload: Option<JsonObject>,
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

/// Parent-launched task-backed subagent/advisor run row.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ParentedRun {
    /// Own task identity.
    pub task_id: TaskId,
    /// Agent-run execution and record identity.
    pub agent_run_id: AgentRunId,
    /// Owning request.
    pub request_id: RequestId,
    /// Lifecycle status.
    pub status: TaskStatus,
    /// Exact parent agent run that launched this run.
    pub parent_agent_run_id: AgentRunId,
    /// Denormalized parent task grouping index.
    pub parent_task_id: TaskId,
    /// Parent-launched run kind.
    pub kind: ParentedAgentRunKind,
    /// Model tool-use id that launched this run, if available.
    #[serde(default)]
    pub tool_use_id: Option<ToolUseId>,
    /// Bound agent profile.
    pub agent_name: AgentName,
    /// Terminal payload projection, if any.
    #[serde(default)]
    pub terminal_payload: Option<JsonObject>,
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
