//! `Attempt` DTO (horizontal-retry axis) and its enums.
//!
//! Ports the Attempt half of `workflow/_core/state.py`. An attempt is one
//! planner-authored plan (a DAG of generator + reducer tasks); the reducer set
//! is the exit gate.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use eos_types::{AttemptId, IterationId, TaskId, UtcDateTime, WorkflowId};

use crate::outcomes::ExecutionTaskOutcome;

/// Stage of an [`Attempt`] (Python `AttemptStage`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum AttemptStage {
    /// Planning the generator/reducer DAG.
    Plan,
    /// Running the planned task set to quiescence.
    Run,
    /// Closed (terminal).
    Closed,
}

/// Outcome status of an [`Attempt`] (Python `AttemptStatus`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum AttemptStatus {
    /// In progress.
    Running,
    /// Passed (reducer gate satisfied).
    Passed,
    /// Failed.
    Failed,
}

/// Why an attempt failed (Python `AttemptFailReason`). Distinct from
/// `PlannerFailReason` (spec §6.10).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum AttemptFailReason {
    /// A task in the plan failed.
    TaskFailed,
    /// The attempt failed to start up.
    StartupFailed,
}

/// Immutable view of a persisted Attempt (Python `state.py:Attempt`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Attempt {
    /// Attempt identifier.
    pub id: AttemptId,
    /// Owning iteration.
    pub iteration_id: IterationId,
    /// Owning workflow.
    pub workflow_id: WorkflowId,
    /// Monotonic per-iteration sequence number (unique).
    pub attempt_sequence_no: i64,
    /// Current stage.
    pub stage: AttemptStage,
    /// Current status.
    pub status: AttemptStatus,
    /// The planner task that authored this attempt's plan, if assigned.
    pub planner_task_id: Option<TaskId>,
    /// The plan's generator task ids.
    pub generator_task_ids: Vec<TaskId>,
    /// The plan's reducer task ids (the exit gate).
    pub reducer_task_ids: Vec<TaskId>,
    /// Goal carried to the next iteration (DB column `deferred_goal`, anchor §4).
    pub deferred_goal_for_next_iteration: Option<String>,
    /// Failure reason, if failed.
    pub fail_reason: Option<AttemptFailReason>,
    /// Creation timestamp.
    pub created_at: UtcDateTime,
    /// Last-update timestamp.
    pub updated_at: UtcDateTime,
    /// Close timestamp, if closed.
    pub closed_at: Option<UtcDateTime>,
    /// Recorded execution outcomes (pre-normalized at the `eos-db` boundary).
    pub outcomes: Vec<ExecutionTaskOutcome>,
}

impl Attempt {
    /// Whether the attempt has reached the closed stage.
    #[must_use]
    pub const fn is_closed(&self) -> bool {
        matches!(self.stage, AttemptStage::Closed)
    }
}
