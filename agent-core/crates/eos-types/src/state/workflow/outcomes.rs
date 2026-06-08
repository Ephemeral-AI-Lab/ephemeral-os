//! Execution-outcome record type and status helpers.
//!
//! Ports the shared outcome DTOs from `workflow/_core/outcomes.py`. Projection
//! algebra that interprets attempts and task stores lives in `eos-workflow`.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::TaskId;

/// Placeholder text for an [`ExecutionTaskOutcome`] outcome with no recorded
/// detail. Shared by the `eos-db` row mapper and the `eos-workflow` context
/// engine so the prompt-facing wording has one source of truth.
pub const NO_OUTCOME: &str = "(no outcome recorded)";

/// Binary status of one execution outcome (Rust `TaskOutcomeStatus`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum TaskOutcomeStatus {
    /// The task completed successfully.
    Success,
    /// The task failed.
    Failed,
}

impl TaskOutcomeStatus {
    /// The canonical `snake_case` token (matches the `serde` wire form), so
    /// prompt-facing rendering shares one source of truth with serialization.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Success => "success",
            Self::Failed => "failed",
        }
    }
}

/// The execution role an outcome belongs to (Rust `ExecutionRole`). Only
/// `generator`/`reducer` execution evidence ever appears in outcomes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionRole {
    /// A generator (execution) task.
    Generator,
    /// A reducer task (the attempt's exit gate).
    Reducer,
}

impl ExecutionRole {
    /// The canonical `snake_case` token (matches the `serde` wire form).
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Generator => "generator",
            Self::Reducer => "reducer",
        }
    }
}

/// One generator/reducer task's terminal execution evidence
/// (Rust `ExecutionTaskOutcome`). Bounded to a single persisted task.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ExecutionTaskOutcome {
    /// Whether the task succeeded or failed.
    pub status: TaskOutcomeStatus,
    /// The execution role that produced this outcome.
    pub role: ExecutionRole,
    /// The task that produced this outcome.
    pub task_id: TaskId,
    /// Free-text outcome summary.
    pub outcome: String,
}

/// Fill a *missing* per-record status from the owning task's raw status string
/// (Rust `present_status`): `"done"` → `Success`, everything else → `Failed`.
///
/// Do **not** apply this to a record status that is already present — that path
/// uses `_normalize_status` at the `eos-db` boundary, where `"done"` → `Failed`
/// (spec §6.8). The two normalizers are distinct.
#[must_use]
pub fn present_status(raw_status: &str) -> TaskOutcomeStatus {
    if raw_status == "done" {
        TaskOutcomeStatus::Success
    } else {
        TaskOutcomeStatus::Failed
    }
}

/// Construct one execution outcome for a terminal submission
/// (Rust `execution_outcome_for_submission`).
#[must_use]
pub fn execution_outcome_for_submission(
    task_id: TaskId,
    role: ExecutionRole,
    status: TaskOutcomeStatus,
    outcome: String,
) -> ExecutionTaskOutcome {
    ExecutionTaskOutcome {
        status,
        role,
        task_id,
        outcome,
    }
}
