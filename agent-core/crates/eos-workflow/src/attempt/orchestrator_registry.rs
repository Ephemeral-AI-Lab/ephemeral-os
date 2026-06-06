use std::collections::HashMap;
use std::sync::Arc;

use eos_state::AttemptId;
use parking_lot::Mutex;
use tokio::task::AbortHandle;

use crate::{Result, WorkflowError};

use super::AttemptOrchestrator;

/// Process-local liveness map for active attempt orchestrators.
#[derive(Default)]
pub struct AttemptOrchestratorRegistry {
    by_attempt_id: Mutex<HashMap<AttemptId, Arc<AttemptOrchestrator>>>,
    /// Abort handles for the per-attempt planner-driver tasks, so a workflow
    /// cancel can tear down an attempt's in-flight runs (the planner task owns
    /// the RUN-stage `JoinSet`). Cleared on settle without aborting.
    planner_aborts: Mutex<HashMap<AttemptId, AbortHandle>>,
}

impl std::fmt::Debug for AttemptOrchestratorRegistry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AttemptOrchestratorRegistry")
            .field("len", &self.by_attempt_id.lock().len())
            .finish()
    }
}

impl AttemptOrchestratorRegistry {
    /// Create an empty registry.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    pub(crate) fn register(&self, orchestrator: Arc<AttemptOrchestrator>) -> Result<()> {
        let mut guard = self.by_attempt_id.lock();
        let attempt_id = orchestrator.attempt_id().clone();
        if let Some(current) = guard.get(&attempt_id) {
            if !Arc::ptr_eq(current, &orchestrator) {
                return Err(WorkflowError::invariant(format!(
                    "attempt orchestrator already registered for attempt {:?}",
                    attempt_id.as_str()
                )));
            }
        }
        guard.insert(attempt_id, orchestrator);
        Ok(())
    }

    /// Look up an active orchestrator.
    #[must_use]
    pub fn get(&self, attempt_id: &AttemptId) -> Option<Arc<AttemptOrchestrator>> {
        self.by_attempt_id.lock().get(attempt_id).cloned()
    }

    /// Record the abort handle of an attempt's planner-driver task.
    pub(crate) fn store_planner_abort(&self, attempt_id: AttemptId, handle: AbortHandle) {
        self.planner_aborts.lock().insert(attempt_id, handle);
    }

    /// Abort an attempt's in-flight planner-driver task (and, transitively, its
    /// RUN-stage runs) if it is still live. Used by the workflow-cancel path; a
    /// no-op once the attempt has settled and cleared its handle.
    pub(crate) fn abort_planner(&self, attempt_id: &AttemptId) {
        if let Some(handle) = self.planner_aborts.lock().remove(attempt_id) {
            handle.abort();
        }
    }

    pub(crate) fn deregister(&self, attempt_id: &AttemptId) {
        self.by_attempt_id.lock().remove(attempt_id);
        // Drop the planner abort handle WITHOUT aborting: normal settlement runs
        // *inside* that task (settle -> close -> deregister), so aborting here
        // would cancel the task mid-finish. Cancellation aborts via
        // `abort_planner` instead.
        self.planner_aborts.lock().remove(attempt_id);
    }
}
