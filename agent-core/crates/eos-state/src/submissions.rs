//! Validated terminal-outcome submission DTOs (tools ↔ workflow contract).
//!
//! Ports `workflow/submissions.py`. `Literal[...]` fields become enums; the
//! generator/reducer `status` reuses [`TaskOutcomeStatus`] (DRY, spec §6.10).

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use eos_types::{AttemptId, JsonObject, TaskId};

use crate::outcomes::TaskOutcomeStatus;

/// Whether a planner submission completes the attempt or defers a goal
/// (Python `Literal["completes","defers"]`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum PlannerKind {
    /// The plan completes the attempt.
    Completes,
    /// The plan defers a goal to the next iteration.
    Defers,
}

/// Why a planner submission failed (Python `Literal["run_exhausted"]`).
/// Distinct from `AttemptFailReason` (spec §6.10).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum PlannerFailReason {
    /// The attempt's run budget was exhausted.
    RunExhausted,
}

/// Validated planner submission from a full or partial plan tool
/// (Python `PlannerSubmission`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct PlannerSubmission {
    /// Owning attempt.
    pub attempt_id: AttemptId,
    /// The planner task that produced this submission.
    pub planner_task_id: TaskId,
    /// Whether the plan completes or defers.
    pub kind: PlannerKind,
    /// The plan's generator task ids.
    pub generator_task_ids: Vec<TaskId>,
    /// The plan's reducer task ids.
    pub reducer_task_ids: Vec<TaskId>,
    /// Goal carried to the next iteration, if deferring (anchor §4).
    pub deferred_goal_for_next_iteration: Option<String>,
}

/// Runtime-synthesized planner failure (Python `PlannerFailureSubmission`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct PlannerFailureSubmission {
    /// Owning attempt.
    pub attempt_id: AttemptId,
    /// The planner task that failed.
    pub planner_task_id: TaskId,
    /// The planner failure reason.
    pub fail_reason: PlannerFailReason,
}

/// Validated terminal outcome for one generator task (Python `GeneratorSubmission`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct GeneratorSubmission {
    /// Owning attempt.
    pub attempt_id: AttemptId,
    /// The generator task.
    pub task_id: TaskId,
    /// Success or failure.
    pub status: TaskOutcomeStatus,
    /// Free-text outcome summary.
    pub outcome: String,
    /// Flattened terminal tool result (always present on a terminal submit).
    pub terminal_tool_result: JsonObject,
}

/// Validated terminal outcome for one reducer task (Python `ReducerSubmission`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ReducerSubmission {
    /// Owning attempt.
    pub attempt_id: AttemptId,
    /// The reducer task.
    pub task_id: TaskId,
    /// Success or failure.
    pub status: TaskOutcomeStatus,
    /// Free-text outcome summary.
    pub outcome: String,
    /// Flattened terminal tool result (always present on a terminal submit).
    pub terminal_tool_result: JsonObject,
}
