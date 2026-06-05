//! `Attempt` DTO (horizontal-retry axis) and its enums.
//!
//! Ports the Attempt half of `workflow/_core/state.py`. An attempt is one
//! planner-authored plan (a DAG of generator + reducer tasks); the reducer set
//! is the exit gate.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use eos_types::{AttemptId, IterationId, TaskId, UtcDateTime, WorkflowId};

use crate::outcomes::ExecutionTaskOutcome;
use crate::plan::{DeferredGoal, MaterializedPlan};

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

impl AttemptStatus {
    /// The canonical `snake_case` token (matches the `serde` wire form).
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Running => "running",
            Self::Passed => "passed",
            Self::Failed => "failed",
        }
    }
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

/// Terminal closure of an [`Attempt`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum AttemptClosure {
    /// Reducer gate passed.
    Passed {
        /// Recorded execution outcomes.
        outcomes: Vec<ExecutionTaskOutcome>,
        /// Close timestamp.
        closed_at: UtcDateTime,
    },
    /// Attempt failed.
    Failed {
        /// Required failure reason.
        reason: AttemptFailReason,
        /// Recorded execution outcomes.
        outcomes: Vec<ExecutionTaskOutcome>,
        /// Close timestamp.
        closed_at: UtcDateTime,
    },
}

impl AttemptClosure {
    /// Closure status.
    #[must_use]
    pub const fn status(&self) -> AttemptStatus {
        match self {
            Self::Passed { .. } => AttemptStatus::Passed,
            Self::Failed { .. } => AttemptStatus::Failed,
        }
    }

    /// Failure reason, if failed.
    #[must_use]
    pub const fn fail_reason(&self) -> Option<AttemptFailReason> {
        match self {
            Self::Passed { .. } => None,
            Self::Failed { reason, .. } => Some(*reason),
        }
    }

    /// Recorded execution outcomes.
    #[must_use]
    pub fn outcomes(&self) -> &[ExecutionTaskOutcome] {
        match self {
            Self::Passed { outcomes, .. } | Self::Failed { outcomes, .. } => outcomes,
        }
    }

    /// Close timestamp.
    #[must_use]
    pub const fn closed_at(&self) -> UtcDateTime {
        match self {
            Self::Passed { closed_at, .. } | Self::Failed { closed_at, .. } => *closed_at,
        }
    }
}

/// Lifecycle state of an [`Attempt`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum AttemptState {
    /// Planner task has not materialized a DAG yet.
    Planning {
        /// Planner task assigned to this attempt, if PLAN has started.
        planner_task_id: Option<TaskId>,
    },
    /// Planner task has materialized the generator/reducer DAG.
    Running {
        /// Materialized persisted plan.
        plan: MaterializedPlan,
    },
    /// Attempt is terminal.
    Closed {
        /// Terminal closure.
        closure: AttemptClosure,
        /// Planner task assigned before close when no materialized plan exists.
        planner_task_id: Option<TaskId>,
        /// Materialized plan, when this attempt reached RUN before closing.
        plan: Option<MaterializedPlan>,
    },
}

impl AttemptState {
    /// Persisted stage view derived from the state.
    #[must_use]
    pub const fn stage(&self) -> AttemptStage {
        match self {
            Self::Planning { .. } => AttemptStage::Plan,
            Self::Running { .. } => AttemptStage::Run,
            Self::Closed { .. } => AttemptStage::Closed,
        }
    }

    /// Persisted status view derived from the state.
    #[must_use]
    pub const fn status(&self) -> AttemptStatus {
        match self {
            Self::Planning { .. } | Self::Running { .. } => AttemptStatus::Running,
            Self::Closed { closure, .. } => closure.status(),
        }
    }

    /// Planner task id, if one is known in this state.
    #[must_use]
    pub const fn planner_task_id(&self) -> Option<&TaskId> {
        match self {
            Self::Planning { planner_task_id } => planner_task_id.as_ref(),
            Self::Running { plan } => Some(&plan.planner_task_id),
            Self::Closed {
                planner_task_id,
                plan,
                ..
            } => match plan {
                Some(plan) => Some(&plan.planner_task_id),
                None => planner_task_id.as_ref(),
            },
        }
    }

    /// Materialized plan, if this state owns one.
    #[must_use]
    pub const fn materialized_plan(&self) -> Option<&MaterializedPlan> {
        match self {
            Self::Planning { .. } => None,
            Self::Running { plan } => Some(plan),
            Self::Closed { plan, .. } => match plan {
                Some(plan) => Some(plan),
                None => None,
            },
        }
    }

    /// Terminal closure, if closed.
    #[must_use]
    pub const fn closure(&self) -> Option<&AttemptClosure> {
        match self {
            Self::Closed { closure, .. } => Some(closure),
            Self::Planning { .. } | Self::Running { .. } => None,
        }
    }
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
    /// Lifecycle state.
    pub state: AttemptState,
    /// Creation timestamp.
    pub created_at: UtcDateTime,
    /// Last-update timestamp.
    pub updated_at: UtcDateTime,
}

impl Attempt {
    /// Persisted stage view.
    #[must_use]
    pub const fn stage(&self) -> AttemptStage {
        self.state.stage()
    }

    /// Persisted status view.
    #[must_use]
    pub const fn status(&self) -> AttemptStatus {
        self.state.status()
    }

    /// Whether the attempt has reached the closed stage.
    #[must_use]
    pub const fn is_closed(&self) -> bool {
        matches!(self.state, AttemptState::Closed { .. })
    }

    /// Planner task id, if one is known.
    #[must_use]
    pub const fn planner_task_id(&self) -> Option<&TaskId> {
        self.state.planner_task_id()
    }

    /// Project the resolved plan, if one has been recorded.
    #[must_use]
    pub const fn materialized_plan(&self) -> Option<&MaterializedPlan> {
        self.state.materialized_plan()
    }

    /// Generator task ids in the materialized plan.
    #[must_use]
    pub fn generator_task_ids(&self) -> &[TaskId] {
        self.materialized_plan()
            .map_or(&[], |plan| plan.generator_task_ids.as_slice())
    }

    /// Reducer task ids in the materialized plan.
    #[must_use]
    pub fn reducer_task_ids(&self) -> &[TaskId] {
        self.materialized_plan()
            .map_or(&[], |plan| plan.reducer_task_ids.as_slice())
    }

    /// Deferred goal carried by the materialized plan.
    #[must_use]
    pub const fn deferred_goal_for_next_iteration(&self) -> Option<&DeferredGoal> {
        match self.materialized_plan() {
            Some(plan) => plan.deferred_goal(),
            None => None,
        }
    }

    /// Terminal closure, if closed.
    #[must_use]
    pub const fn closure(&self) -> Option<&AttemptClosure> {
        self.state.closure()
    }

    /// Failure reason, if failed.
    #[must_use]
    pub const fn fail_reason(&self) -> Option<AttemptFailReason> {
        match self.closure() {
            Some(closure) => closure.fail_reason(),
            None => None,
        }
    }

    /// Close timestamp, if closed.
    #[must_use]
    pub const fn closed_at(&self) -> Option<UtcDateTime> {
        match self.closure() {
            Some(closure) => Some(closure.closed_at()),
            None => None,
        }
    }

    /// Recorded execution outcomes (pre-normalized at the `eos-db` boundary).
    #[must_use]
    pub fn outcomes(&self) -> &[ExecutionTaskOutcome] {
        match self.closure() {
            Some(closure) => closure.outcomes(),
            None => &[],
        }
    }
}
