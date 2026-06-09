//! Grouped durable store handles for request lifecycle operations.

use std::sync::Arc;

use eos_types::{AttemptStore, IterationStore, RequestStore, TaskAgentRunStore, WorkflowStore};

/// Durable store handles used by request operations.
#[derive(Clone)]
pub(crate) struct RequestState {
    pub(crate) request_store: Arc<dyn RequestStore>,
    pub(crate) task_agent_run_store: Arc<dyn TaskAgentRunStore>,
    pub(crate) workflow_store: Arc<dyn WorkflowStore>,
    pub(crate) iteration_store: Arc<dyn IterationStore>,
    pub(crate) attempt_store: Arc<dyn AttemptStore>,
}

impl std::fmt::Debug for RequestState {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("RequestState")
            .finish_non_exhaustive()
    }
}
