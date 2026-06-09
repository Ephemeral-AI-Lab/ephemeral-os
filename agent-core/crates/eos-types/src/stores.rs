//! Shared async persistence stores grouped by consuming behavior boundary.

mod engine;
mod model_registry;
mod request_task;
mod task_agent_run;
mod workflow;

pub use engine::AgentRunStore;
pub use model_registry::ModelStore;
pub use request_task::{RequestStore, TaskStore};
pub use task_agent_run::{parented_task_id, root_task_id, TaskAgentRunStore};
pub use workflow::{AttemptStore, IterationStore, WorkflowStore};

/// Alias for the error every store method returns.
pub type StoreError = crate::CoreError;

/// Sealing marker for the store traits.
///
/// Implemented by workspace repository types and in-crate test fakes only.
#[doc(hidden)]
pub trait Sealed {}
