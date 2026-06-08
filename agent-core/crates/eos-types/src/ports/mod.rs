//! Shared async persistence ports grouped by consuming behavior boundary.

mod engine;
mod model_registry;
mod runtime;
mod workflow;

pub use engine::AgentRunStore;
pub use model_registry::ModelStore;
pub use runtime::{RequestStore, TaskStore};
pub use workflow::{AttemptStore, IterationStore, WorkflowStore};

/// Alias for the error every store method returns.
pub type StoreError = crate::CoreError;

/// Sealing marker for the store traits.
///
/// Implemented by workspace repository types and in-crate test fakes only.
#[doc(hidden)]
pub trait Sealed {}
