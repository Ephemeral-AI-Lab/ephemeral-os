//! Type-driven planner values shared by tools, workflow, and persistence.

use std::num::NonZeroU32;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{CoreError, TaskId};

/// Planner-local DAG node id.
#[derive(
    Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize, JsonSchema,
)]
#[serde(transparent)]
pub struct PlanNodeId(String);

impl PlanNodeId {
    /// Construct a nonblank planner-local node id.
    ///
    /// # Errors
    /// Returns [`CoreError`] when the id is blank.
    pub fn new(value: impl Into<String>) -> Result<Self, CoreError> {
        let value = value.into();
        if value.trim().is_empty() {
            return Err(CoreError::Store("plan node id must be nonblank".to_owned()));
        }
        Ok(Self(value))
    }

    /// Borrow the raw node id.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// Consume the value and return the raw node id.
    #[must_use]
    pub fn into_string(self) -> String {
        self.0
    }
}

impl std::fmt::Display for PlanNodeId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(f)
    }
}

impl TryFrom<String> for PlanNodeId {
    type Error = CoreError;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        Self::new(value)
    }
}

impl TryFrom<&str> for PlanNodeId {
    type Error = CoreError;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        Self::new(value.to_owned())
    }
}

/// Goal carried to the next workflow iteration.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct DeferredGoal(String);

impl DeferredGoal {
    /// Construct a nonblank deferred goal.
    ///
    /// # Errors
    /// Returns [`CoreError`] when the goal is blank.
    pub fn new(value: impl Into<String>) -> Result<Self, CoreError> {
        let value = value.into();
        if value.trim().is_empty() {
            return Err(CoreError::Store(
                "deferred goal must be nonblank".to_owned(),
            ));
        }
        Ok(Self(value))
    }

    /// Borrow the raw goal text.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// Consume the value and return the raw goal text.
    #[must_use]
    pub fn into_string(self) -> String {
        self.0
    }
}

impl std::fmt::Display for DeferredGoal {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(f)
    }
}

impl TryFrom<String> for DeferredGoal {
    type Error = CoreError;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        Self::new(value)
    }
}

impl TryFrom<&str> for DeferredGoal {
    type Error = CoreError;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        Self::new(value.to_owned())
    }
}

/// Planner disposition after the current iteration's reducers complete.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum PlanDisposition {
    /// The plan covers the current iteration goal.
    Complete,
    /// The plan intentionally defers remaining current-iteration items.
    Defer(DeferredGoal),
}

impl PlanDisposition {
    /// Build a disposition from an optional typed deferred goal.
    #[must_use]
    pub fn from_deferred_goal(goal: Option<DeferredGoal>) -> Self {
        match goal {
            Some(goal) => Self::Defer(goal),
            None => Self::Complete,
        }
    }

    /// The legacy planner-result label stored in terminal result metadata.
    #[must_use]
    pub const fn kind_label(&self) -> &'static str {
        match self {
            Self::Complete => "completes",
            Self::Defer(_) => "defers",
        }
    }

    /// The model-facing submission kind label.
    #[must_use]
    pub const fn submission_kind_label(&self) -> &'static str {
        match self {
            Self::Complete => "planner_completes",
            Self::Defer(_) => "planner_defers",
        }
    }

    /// Deferred goal, if this disposition continues the workflow.
    #[must_use]
    pub const fn deferred_goal(&self) -> Option<&DeferredGoal> {
        match self {
            Self::Complete => None,
            Self::Defer(goal) => Some(goal),
        }
    }
}

/// Validated attempt budget.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(transparent)]
pub struct AttemptBudget(NonZeroU32);

impl AttemptBudget {
    /// Construct a budget from a nonzero count.
    #[must_use]
    pub const fn new(value: NonZeroU32) -> Self {
        Self(value)
    }

    /// Try to construct a budget from a `u32`.
    ///
    /// # Errors
    /// Returns [`CoreError`] when the count is zero.
    pub fn try_from_u32(value: u32) -> Result<Self, CoreError> {
        NonZeroU32::new(value)
            .map(Self)
            .ok_or_else(|| CoreError::Store("attempt budget must be greater than zero".to_owned()))
    }

    /// Try to construct a budget from the database integer representation.
    ///
    /// # Errors
    /// Returns [`CoreError`] when the count is zero, negative, or too large.
    pub fn try_from_i64(value: i64) -> Result<Self, CoreError> {
        let value = u32::try_from(value).map_err(|_| {
            CoreError::Store("attempt budget must fit u32 and be greater than zero".to_owned())
        })?;
        Self::try_from_u32(value)
    }

    /// Return the budget as a plain count.
    #[must_use]
    pub const fn get(self) -> u32 {
        self.0.get()
    }

    /// Return the database integer representation.
    #[must_use]
    pub const fn as_i64(self) -> i64 {
        self.0.get() as i64
    }
}

impl Default for AttemptBudget {
    fn default() -> Self {
        Self(NonZeroU32::new(2).unwrap_or(NonZeroU32::MIN))
    }
}

impl TryFrom<u32> for AttemptBudget {
    type Error = CoreError;

    fn try_from(value: u32) -> Result<Self, Self::Error> {
        Self::try_from_u32(value)
    }
}

impl TryFrom<i64> for AttemptBudget {
    type Error = CoreError;

    fn try_from(value: i64) -> Result<Self, Self::Error> {
        Self::try_from_i64(value)
    }
}

/// Resolved persisted task ids for a planner-authored attempt plan.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct MaterializedPlan {
    /// Planner task that authored the plan.
    pub planner_task_id: TaskId,
    /// Whether the plan completes or defers.
    pub disposition: PlanDisposition,
    /// Persisted generator task ids.
    pub generator_task_ids: Vec<TaskId>,
    /// Persisted reducer task ids.
    pub reducer_task_ids: Vec<TaskId>,
}

impl MaterializedPlan {
    /// Deferred goal, if the materialized plan continues the workflow.
    #[must_use]
    pub const fn deferred_goal(&self) -> Option<&DeferredGoal> {
        self.disposition.deferred_goal()
    }
}
