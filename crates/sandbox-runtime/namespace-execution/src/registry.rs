use std::collections::HashMap;
use std::sync::Mutex;

use crate::error::NamespaceExecutionError;
use crate::id::NamespaceExecutionId;
use crate::status::NamespaceExecutionTerminalStatus;

/// Live + completed executions keyed by `NamespaceExecutionId`, with admission.
/// Shared as `Arc<ExecutionRegistry>`; the watcher thread calls `complete`.
pub struct ExecutionRegistry {
    inner: Mutex<RegistryState>,
    max_active: usize,
}

#[derive(Default)]
struct RegistryState {
    live: HashMap<NamespaceExecutionId, LiveExecution>,
    completed: HashMap<NamespaceExecutionId, CompletedExecution>,
}

struct LiveExecution {
    pgid: Option<i32>,
}

/// Terminal projection retained after an execution leaves the live set. Generic
/// — no command types (transcript cursor / session disposition are Phase 3).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CompletedExecution {
    pub status: NamespaceExecutionTerminalStatus,
    pub exit_code: Option<i64>,
}

impl ExecutionRegistry {
    #[must_use]
    pub fn new(max_active: usize) -> Self {
        Self {
            inner: Mutex::new(RegistryState::default()),
            max_active,
        }
    }

    #[must_use]
    pub fn max_active(&self) -> usize {
        self.max_active
    }

    /// Atomically reserve a live slot keyed by `id`; `Err(Admission)` if full.
    /// The capacity check and the insert happen under one lock, so concurrent
    /// `run_*` calls cannot both admit the last slot.
    pub fn try_reserve(&self, id: &NamespaceExecutionId) -> Result<(), NamespaceExecutionError> {
        let mut state = self.lock();
        if state.live.len() >= self.max_active {
            return Err(NamespaceExecutionError::Admission {
                max_active: self.max_active,
            });
        }
        state.live.insert(id.clone(), LiveExecution { pgid: None });
        Ok(())
    }

    /// Enrich a reserved slot with the spawned process group (the cancel handle
    /// Phase 5 reads); a no-op if the slot already left the live set.
    pub fn attach(&self, id: &NamespaceExecutionId, pgid: Option<i32>) {
        if let Some(live) = self.lock().live.get_mut(id) {
            live.pgid = pgid;
        }
    }

    /// Release a reservation on spawn failure.
    pub fn abort(&self, id: &NamespaceExecutionId) {
        self.lock().live.remove(id);
    }

    /// Move an execution live → completed under the single lock.
    pub fn complete(&self, id: &NamespaceExecutionId, done: CompletedExecution) {
        let mut state = self.lock();
        state.live.remove(id);
        state.completed.insert(id.clone(), done);
    }

    #[must_use]
    pub fn is_live(&self, id: &NamespaceExecutionId) -> bool {
        self.lock().live.contains_key(id)
    }

    #[must_use]
    pub fn is_completed(&self, id: &NamespaceExecutionId) -> bool {
        self.lock().completed.contains_key(id)
    }

    /// The process group attached to a live slot, if any.
    #[must_use]
    pub fn live_pgid(&self, id: &NamespaceExecutionId) -> Option<i32> {
        self.lock().live.get(id).and_then(|live| live.pgid)
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, RegistryState> {
        self.inner
            .lock()
            .expect("execution registry mutex poisoned")
    }
}
