use eos_types::{
    AttemptBudget, AttemptId, GeneratorId, PlannerId, ReducerId, TaskId, WorkflowNodeId,
};

use crate::{Result, WorkflowError};

/// Per-workflow lifecycle knobs injected by backend composition.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct WorkflowLifecycleConfig {
    /// Attempts allowed per iteration before the iteration closes failed.
    pub default_attempt_budget: AttemptBudget,
}

pub(crate) fn planner_id() -> PlannerId {
    PlannerId::new("planner").expect("static planner id is nonblank")
}

/// Stable planner task id for an attempt.
pub fn planner_task_id(attempt_id: &AttemptId) -> Result<TaskId> {
    planner_task_id_for(attempt_id, &planner_id())
}

pub(crate) fn planner_task_id_for(
    attempt_id: &AttemptId,
    planner_id: &PlannerId,
) -> Result<TaskId> {
    Ok(eos_types::workflow_task_id(
        attempt_id,
        &WorkflowNodeId::Planner {
            planner_id: planner_id.clone(),
        },
    )?)
}

/// Stable generator task id from an attempt id and generator id.
pub fn generator_task_id(attempt_id: &AttemptId, generator_id: &GeneratorId) -> Result<TaskId> {
    Ok(eos_types::workflow_task_id(
        attempt_id,
        &WorkflowNodeId::Generator {
            generator_id: generator_id.clone(),
        },
    )?)
}

/// Stable reducer task id from an attempt id and reducer id.
pub fn reducer_task_id(attempt_id: &AttemptId, reducer_id: &ReducerId) -> Result<TaskId> {
    Ok(eos_types::workflow_task_id(
        attempt_id,
        &WorkflowNodeId::Reducer {
            reducer_id: reducer_id.clone(),
        },
    )?)
}

pub(crate) fn generator_id_from_task_id(
    attempt_id: &AttemptId,
    task_id: &TaskId,
) -> Result<GeneratorId> {
    let prefix = format!("{}:gen:", attempt_id.as_str());
    let local_id = task_id.as_str().strip_prefix(&prefix).ok_or_else(|| {
        WorkflowError::invariant(format!(
            "generator task id {:?} is not anchored to attempt {:?}",
            task_id.as_str(),
            attempt_id.as_str()
        ))
    })?;
    GeneratorId::new(local_id).map_err(|err| {
        WorkflowError::invariant(format!(
            "invalid generator id {:?} in task id {:?}: {err}",
            local_id,
            task_id.as_str()
        ))
    })
}

pub(crate) fn reducer_id_from_task_id(
    attempt_id: &AttemptId,
    task_id: &TaskId,
) -> Result<ReducerId> {
    let prefix = format!("{}:red:", attempt_id.as_str());
    let local_id = task_id.as_str().strip_prefix(&prefix).ok_or_else(|| {
        WorkflowError::invariant(format!(
            "reducer task id {:?} is not anchored to attempt {:?}",
            task_id.as_str(),
            attempt_id.as_str()
        ))
    })?;
    ReducerId::new(local_id).map_err(|err| {
        WorkflowError::invariant(format!(
            "invalid reducer id {:?} in task id {:?}: {err}",
            local_id,
            task_id.as_str()
        ))
    })
}
