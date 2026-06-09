//! Shared persisted state DTOs grouped by their behavior owner.

pub mod engine;
pub mod model_registry;
pub mod request_task;
pub mod tools;
pub mod workflow;

pub use engine::AgentRun;
pub use model_registry::ModelRegistration;
pub use request_task::{Page, PageResult, Request, RequestListFilter, RequestStatus};
pub use request_task::{ParentedRun, Task, TaskRole, TaskRun, TaskStatus, TASK_AGENT_ROLES};
pub use tools::{
    BackgroundSessionCounts, GeneratorSubmission, PlannerFailReason, PlannerFailureSubmission,
    PlannerSubmission, ReducerSubmission,
};
pub use workflow::{
    execution_outcome_for_submission, present_status, Attempt, AttemptBudget, AttemptClosure,
    AttemptFailReason, AttemptStage, AttemptState, AttemptStatus, DeferredGoal, ExecutionRole,
    ExecutionTaskOutcome, Iteration, IterationCreationReason, IterationOutcome, IterationStatus,
    MaterializedPlan, PlanDisposition, PlanNodeId, TaskOutcomeStatus, Workflow, WorkflowOutcome,
    WorkflowStatus, NO_OUTCOME,
};
