use std::collections::HashMap;
use std::sync::Arc;

use eos_types::{AgentRunId, AttemptId, IterationId};
use parking_lot::Mutex;

use crate::iteration_run::IterationRunCoordinator;
use crate::{Result, WorkflowError};

use super::AttemptRun;

/// Process-local liveness map for active attempt runs.
#[derive(Default)]
pub struct ActiveAttemptRuns {
    by_attempt_id: Mutex<HashMap<AttemptId, Arc<AttemptRun>>>,
    by_agent_run_id: Mutex<HashMap<AgentRunId, Arc<AttemptRun>>>,
}

impl std::fmt::Debug for ActiveAttemptRuns {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ActiveAttemptRuns")
            .field("attempts", &self.by_attempt_id.lock().len())
            .field("agent_runs", &self.by_agent_run_id.lock().len())
            .finish()
    }
}

impl ActiveAttemptRuns {
    /// Create an empty registry.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    pub(crate) fn register(&self, run: Arc<AttemptRun>) -> Result<()> {
        let mut guard = self.by_attempt_id.lock();
        let attempt_id = run.attempt_id().clone();
        if let Some(current) = guard.get(&attempt_id) {
            if !Arc::ptr_eq(current, &run) {
                return Err(WorkflowError::invariant(format!(
                    "attempt run already registered for attempt {:?}",
                    attempt_id.as_str()
                )));
            }
        }
        guard.insert(attempt_id, run);
        Ok(())
    }

    pub(crate) fn register_agent_run(
        &self,
        agent_run_id: AgentRunId,
        run: Arc<AttemptRun>,
    ) -> Result<()> {
        let mut guard = self.by_agent_run_id.lock();
        if let Some(current) = guard.get(&agent_run_id) {
            if !Arc::ptr_eq(current, &run) {
                return Err(WorkflowError::invariant(format!(
                    "agent run {:?} is already registered for another attempt",
                    agent_run_id.as_str()
                )));
            }
        }
        guard.insert(agent_run_id, run);
        Ok(())
    }

    /// Look up an active attempt run.
    #[must_use]
    pub fn get(&self, attempt_id: &AttemptId) -> Option<Arc<AttemptRun>> {
        self.by_attempt_id.lock().get(attempt_id).cloned()
    }

    /// Look up the active attempt run that owns an agent run.
    #[must_use]
    pub fn get_by_agent_run_id(&self, agent_run_id: &AgentRunId) -> Option<Arc<AttemptRun>> {
        self.by_agent_run_id.lock().get(agent_run_id).cloned()
    }

    pub(crate) fn deregister(&self, attempt_id: &AttemptId) {
        let Some(run) = self.by_attempt_id.lock().remove(attempt_id) else {
            return;
        };
        self.by_agent_run_id
            .lock()
            .retain(|_, current| !Arc::ptr_eq(current, &run));
    }
}

/// Process-local one-coordinator-per-open-iteration registry.
#[derive(Default)]
pub struct OpenIterationCoordinatorRegistry {
    by_iteration_id: Mutex<HashMap<IterationId, Arc<IterationRunCoordinator>>>,
}

impl std::fmt::Debug for OpenIterationCoordinatorRegistry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("OpenIterationCoordinatorRegistry")
            .field("len", &self.by_iteration_id.lock().len())
            .finish()
    }
}

impl OpenIterationCoordinatorRegistry {
    /// Create an empty registry.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    pub(crate) fn register(&self, coordinator: Arc<IterationRunCoordinator>) -> Result<()> {
        let mut guard = self.by_iteration_id.lock();
        if guard.contains_key(coordinator.iteration_id()) {
            return Err(WorkflowError::invariant(format!(
                "iteration run coordinator already registered for iteration {:?}",
                coordinator.iteration_id().as_str()
            )));
        }
        guard.insert(coordinator.iteration_id().clone(), coordinator);
        Ok(())
    }

    /// Look up a coordinator.
    #[must_use]
    pub(crate) fn get(&self, iteration_id: &IterationId) -> Option<Arc<IterationRunCoordinator>> {
        self.by_iteration_id.lock().get(iteration_id).cloned()
    }

    pub(crate) fn deregister(&self, iteration_id: &IterationId) {
        self.by_iteration_id.lock().remove(iteration_id);
    }
}
