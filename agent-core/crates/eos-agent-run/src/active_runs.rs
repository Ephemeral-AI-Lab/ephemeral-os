//! In-process active agent-run registry.

use std::collections::HashMap;
use std::sync::Arc;

use eos_types::AgentRunId;
use tokio::sync::{watch, Mutex};
use tokio::task::AbortHandle;

use crate::{AgentRunError, AgentRunOutcome};

/// Live process-local registry of spawned agent runs.
#[derive(Debug, Clone, Default)]
pub struct ActiveAgentRuns {
    runs: Arc<Mutex<HashMap<AgentRunId, ActiveAgentRun>>>,
}

/// One active agent run's process-local handles.
#[derive(Debug, Clone)]
pub struct ActiveAgentRun {
    /// Abort handle for the spawned task.
    pub abort_handle: AbortHandle,
    /// Completion signal. `Some` is terminal and is published exactly once.
    pub outcome_tx: watch::Sender<Option<AgentRunOutcome>>,
}

impl ActiveAgentRuns {
    /// Insert one active run.
    pub async fn insert(&self, agent_run_id: AgentRunId, run: ActiveAgentRun) {
        self.runs.lock().await.insert(agent_run_id, run);
    }

    /// Remove an active run.
    pub async fn remove(&self, agent_run_id: &AgentRunId) -> Option<ActiveAgentRun> {
        self.runs.lock().await.remove(agent_run_id)
    }

    /// Return the currently published outcome for an active run, if any.
    pub async fn current_outcome(&self, agent_run_id: &AgentRunId) -> Option<AgentRunOutcome> {
        self.runs
            .lock()
            .await
            .get(agent_run_id)
            .and_then(|run| run.outcome_tx.borrow().clone())
    }

    /// Subscribe to an active run's terminal outcome.
    ///
    /// # Errors
    /// Returns [`AgentRunError::NotActiveInProcess`] when this process does not
    /// own the run.
    pub async fn subscribe(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<watch::Receiver<Option<AgentRunOutcome>>, AgentRunError> {
        self.runs
            .lock()
            .await
            .get(agent_run_id)
            .map(|run| run.outcome_tx.subscribe())
            .ok_or_else(|| AgentRunError::NotActiveInProcess(agent_run_id.clone()))
    }

    /// Wait on an active run's watch channel.
    ///
    /// # Errors
    /// Returns when the run is missing from this process or its sender closes
    /// before a terminal outcome is published.
    pub async fn wait_for(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<AgentRunOutcome, AgentRunError> {
        let mut rx = self.subscribe(agent_run_id).await?;
        loop {
            if let Some(outcome) = rx.borrow().clone() {
                return Ok(outcome);
            }
            rx.changed()
                .await
                .map_err(|_| AgentRunError::CompletionChannelClosed(agent_run_id.clone()))?;
        }
    }
}
