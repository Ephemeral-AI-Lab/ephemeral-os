//! `Iteration` DTO (vertical-continuation axis) and its enums.
//!
//! Ports the Iteration half of `workflow/_core/state.py`.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use eos_types::{AttemptId, IterationId, UtcDateTime, WorkflowId};

/// Lifecycle status of an [`Iteration`] (Python `IterationStatus`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum IterationStatus {
    /// Running; not yet closed.
    Open,
    /// Closed successfully.
    Succeeded,
    /// Closed with failure.
    Failed,
    /// Closed by cancellation.
    Cancelled,
}

/// Why an iteration was created (Python `IterationCreationReason`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum IterationCreationReason {
    /// The first iteration of a workflow.
    Initial,
    /// A continuation iteration spawned from a prior iteration's deferred goal.
    DeferredGoalContinuation,
}

/// Immutable view of a persisted Iteration (Python `state.py:Iteration`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Iteration {
    /// Iteration identifier.
    pub id: IterationId,
    /// Owning workflow.
    pub workflow_id: WorkflowId,
    /// Monotonic per-workflow sequence number (unique).
    pub sequence_no: i64,
    /// Why this iteration was created.
    pub creation_reason: IterationCreationReason,
    /// The iteration goal (DB column `goal`; mapped in `eos-db`, anchor §4).
    pub iteration_goal: String,
    /// Maximum number of attempts allowed in this iteration.
    pub attempt_budget: i64,
    /// Lifecycle status.
    pub status: IterationStatus,
    /// Ordered child attempt ids.
    pub attempt_ids: Vec<AttemptId>,
    /// Goal carried to the next iteration (DB column `deferred_goal`, anchor §4).
    pub deferred_goal_for_next_iteration: Option<String>,
    /// Creation timestamp.
    pub created_at: UtcDateTime,
    /// Last-update timestamp.
    pub updated_at: UtcDateTime,
    /// Close timestamp, if closed.
    pub closed_at: Option<UtcDateTime>,
    /// Serialized canonical projection (a `json.dumps` list); `None` while open.
    pub outcomes: Option<String>,
}

impl Iteration {
    /// Whether the iteration is still open.
    #[must_use]
    pub const fn is_open(&self) -> bool {
        matches!(self.status, IterationStatus::Open)
    }

    /// Number of attempts created so far.
    #[must_use]
    pub fn attempt_count(&self) -> usize {
        self.attempt_ids.len()
    }

    /// Whether the attempt budget still allows another attempt.
    #[must_use]
    pub fn has_budget_remaining(&self) -> bool {
        (self.attempt_count() as i64) < self.attempt_budget
    }

    /// The most recently created attempt id, if any.
    #[must_use]
    pub fn latest_attempt_id(&self) -> Option<&AttemptId> {
        self.attempt_ids.last()
    }
}
