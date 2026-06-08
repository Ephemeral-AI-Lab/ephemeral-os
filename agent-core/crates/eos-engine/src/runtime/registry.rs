//! [`AgentRunRegistry`] — the live address book of in-process agent runs (spec
//! §6.4).
//!
//! Recursive cancellation (`cancel_task` / `cancel_agent_run`) resolves live runs
//! through this registry instead of relying on backend future-dropping. A run is
//! registered before its provider loop starts and removed only after terminal or
//! cancellation finalization completes.
//!
//! The `Running -> Claimed` transition under the registry lock is the single CAS
//! that arbitrates finalization: both natural completion and `cancel_agent_run`
//! call [`AgentRunRegistry::begin_cancel`] to claim the entry; the winner
//! finalizes (Done vs Cancelled) and calls [`AgentRunRegistry::finish_cancel`];
//! the loser sees `None` and no-ops. This prevents a double-finalize when a run
//! completes naturally just as an external cancel arrives.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use eos_types::{AgentRunId, TaskId};

use super::control::AgentRunControl;

/// Shared, cloneable handle to the in-process agent-run registry.
#[derive(Clone, Default)]
pub struct AgentRunRegistry {
    inner: Arc<Mutex<AgentRunRegistryState>>,
}

impl std::fmt::Debug for AgentRunRegistry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let live = self
            .inner
            .lock()
            .map(|guard| guard.by_run_id.len())
            .unwrap_or(0);
        f.debug_struct("AgentRunRegistry")
            .field("entries", &live)
            .finish_non_exhaustive()
    }
}

#[derive(Default)]
struct AgentRunRegistryState {
    by_run_id: HashMap<AgentRunId, AgentRunEntry>,
    by_task_id: HashMap<TaskId, AgentRunId>,
}

enum AgentRunEntry {
    /// A live run that can still be addressed and claimed.
    Running(Arc<AgentRunControl>),
    /// The run has been claimed for finalization (natural or cancellation);
    /// further claims no-op until the entry is removed.
    Claimed,
}

impl AgentRunRegistry {
    /// A fresh, empty registry.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Register a live run. Indexes `by_task_id` only for persisted runs.
    pub fn insert(&self, control: Arc<AgentRunControl>) {
        let mut guard = self.inner.lock().expect("registry lock");
        let agent_run_id = control.agent_run_id().clone();
        if let Some(task_id) = control.task_id() {
            guard
                .by_task_id
                .insert(task_id.clone(), agent_run_id.clone());
        }
        guard
            .by_run_id
            .insert(agent_run_id, AgentRunEntry::Running(control));
    }

    /// Look up a live (not-yet-claimed) run.
    #[must_use]
    pub fn get(&self, agent_run_id: &AgentRunId) -> Option<Arc<AgentRunControl>> {
        match self
            .inner
            .lock()
            .expect("registry lock")
            .by_run_id
            .get(agent_run_id)
        {
            Some(AgentRunEntry::Running(control)) => Some(control.clone()),
            _ => None,
        }
    }

    /// Resolve the live agent run that owns a task, if any.
    #[must_use]
    pub fn agent_run_for_task(&self, task_id: &TaskId) -> Option<AgentRunId> {
        self.inner
            .lock()
            .expect("registry lock")
            .by_task_id
            .get(task_id)
            .cloned()
    }

    /// Claim a run for finalization under the registry lock: flip
    /// `Running -> Claimed` and return the control. A second caller (the loser of
    /// the natural-vs-cancel race) sees the entry already `Claimed` and gets
    /// `None`, making repeated claims idempotent no-ops.
    #[must_use]
    pub fn begin_cancel(&self, agent_run_id: &AgentRunId) -> Option<Arc<AgentRunControl>> {
        let mut guard = self.inner.lock().expect("registry lock");
        match guard.by_run_id.get_mut(agent_run_id) {
            Some(entry @ AgentRunEntry::Running(_)) => {
                let claimed = std::mem::replace(entry, AgentRunEntry::Claimed);
                match claimed {
                    AgentRunEntry::Running(control) => Some(control),
                    AgentRunEntry::Claimed => unreachable!("matched Running above"),
                }
            }
            _ => None,
        }
    }

    /// Remove a finalized run from the registry (both indices).
    pub fn finish_cancel(&self, agent_run_id: &AgentRunId) {
        let mut guard = self.inner.lock().expect("registry lock");
        guard.by_run_id.remove(agent_run_id);
        guard.by_task_id.retain(|_, run_id| run_id != agent_run_id);
    }
}
