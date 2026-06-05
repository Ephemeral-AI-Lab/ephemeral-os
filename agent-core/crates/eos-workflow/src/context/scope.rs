use eos_state::{AttemptId, IterationId, TaskId, WorkflowId};

use super::ContextRole;

/// Identity a context builder reads, keyed by launch role so each role carries
/// exactly the ids it requires. Constructed only through the `for_*`
/// constructors, which makes an id/role mismatch unrepresentable.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ContextScope {
    /// Planner launch: workflow + iteration + attempt.
    Planner {
        /// Workflow id.
        workflow_id: WorkflowId,
        /// Iteration id.
        iteration_id: IterationId,
        /// Attempt id.
        attempt_id: AttemptId,
    },
    /// Generator launch: planner ids plus the assigned task.
    Generator {
        /// Workflow id.
        workflow_id: WorkflowId,
        /// Iteration id.
        iteration_id: IterationId,
        /// Attempt id.
        attempt_id: AttemptId,
        /// Assigned task id.
        task_id: TaskId,
    },
    /// Reducer launch: planner ids plus the assigned task.
    Reducer {
        /// Workflow id.
        workflow_id: WorkflowId,
        /// Iteration id.
        iteration_id: IterationId,
        /// Attempt id.
        attempt_id: AttemptId,
        /// Assigned task id.
        task_id: TaskId,
    },
}

impl ContextScope {
    /// Scope for a planner launch.
    #[must_use]
    pub fn for_planner(
        workflow_id: WorkflowId,
        iteration_id: IterationId,
        attempt_id: AttemptId,
    ) -> Self {
        Self::Planner {
            workflow_id,
            iteration_id,
            attempt_id,
        }
    }

    /// Scope for a generator launch.
    #[must_use]
    pub fn for_generator(
        workflow_id: WorkflowId,
        iteration_id: IterationId,
        attempt_id: AttemptId,
        task_id: TaskId,
    ) -> Self {
        Self::Generator {
            workflow_id,
            iteration_id,
            attempt_id,
            task_id,
        }
    }

    /// Scope for a reducer launch.
    #[must_use]
    pub fn for_reducer(
        workflow_id: WorkflowId,
        iteration_id: IterationId,
        attempt_id: AttemptId,
        task_id: TaskId,
    ) -> Self {
        Self::Reducer {
            workflow_id,
            iteration_id,
            attempt_id,
            task_id,
        }
    }

    /// The launch role this scope was built for.
    #[must_use]
    pub fn role(&self) -> ContextRole {
        match self {
            Self::Planner { .. } => ContextRole::Planner,
            Self::Generator { .. } => ContextRole::Generator,
            Self::Reducer { .. } => ContextRole::Reducer,
        }
    }
}
