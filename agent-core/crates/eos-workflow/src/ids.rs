use eos_types::{AttemptBudget, AttemptId, PlanNodeId, TaskId};

use crate::Result;

/// Per-workflow lifecycle knobs injected by `eos-runtime`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct WorkflowLifecycleConfig {
    /// Attempts allowed per iteration before the iteration closes failed.
    pub default_attempt_budget: AttemptBudget,
}

/// Stable planner task id for an attempt.
pub fn planner_task_id(attempt_id: &AttemptId) -> Result<TaskId> {
    Ok(format!("{}:planner", attempt_id.as_str()).parse()?)
}

/// Stable generator task id from an attempt id and planner-local id.
pub fn generator_task_id(attempt_id: &AttemptId, local_task_id: &PlanNodeId) -> Result<TaskId> {
    Ok(format!("{}:gen:{}", attempt_id.as_str(), local_task_id.as_str()).parse()?)
}

/// Stable reducer task id from an attempt id and planner-local id.
pub fn reducer_task_id(attempt_id: &AttemptId, local_task_id: &PlanNodeId) -> Result<TaskId> {
    Ok(format!("{}:red:{}", attempt_id.as_str(), local_task_id.as_str()).parse()?)
}
