//! eos-state — pure agent-core domain state, outcome projections, terminal
//! submission DTOs, and the per-entity async `Store` traits.
//!
//! This is the upstream domain contract that `eos-db` implements and that
//! `eos-tools`/`eos-engine`/`eos-workflow`/`eos-runtime` consume. It defines
//! *what is stored and what shapes flow between layers*; it never executes I/O.
//! See `docs/plans/backend_agent_core_rust_migration/impl-eos-state.md`.
#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod agent_run;
mod attempt;
mod iteration;
mod model;
mod outcomes;
mod request;
mod store;
mod submissions;
mod task;
mod workflow;

#[cfg(test)]
mod fakes;

pub use agent_run::AgentRun;
pub use attempt::{Attempt, AttemptFailReason, AttemptStage, AttemptStatus};
pub use iteration::{Iteration, IterationCreationReason, IterationStatus};
pub use model::ModelRegistration;
pub use outcomes::{
    attempt_execution_outcomes, execution_outcome_for_submission, latest_iteration, present_status,
    project_attempt_outcomes, project_iteration_outcomes, ExecutionRole, ExecutionTaskOutcome,
    TaskOutcomeStatus,
};
pub use request::{Request, RequestStatus};
pub use store::{
    AgentRunStore, AttemptStore, IterationStore, ModelStore, RequestStore, Sealed, StoreError,
    TaskStore, WorkflowStore,
};
pub use submissions::{
    GeneratorSubmission, PlannerFailReason, PlannerFailureSubmission, PlannerKind,
    PlannerSubmission, ReducerSubmission,
};
pub use task::{Task, TaskRole, TaskStatus, TASK_AGENT_ROLES};
pub use workflow::{Workflow, WorkflowStatus};

// Re-export the upstream value primitives that appear in this crate's public
// API so downstream crates (notably `eos-db`) can name them without a direct
// `eos-types` dependency edge, preserving the `eos-db -> {state, config}` topology.
pub use eos_types::{
    AgentRunId, AttemptId, CoreError, IterationId, JsonObject, RequestId, SandboxId, TaskId,
    UtcDateTime, WorkflowId,
};

#[cfg(test)]
#[path = "../tests/unit/mod.rs"]
mod tests;
