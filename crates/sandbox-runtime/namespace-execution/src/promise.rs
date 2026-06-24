use std::sync::{Condvar, Mutex};

use crate::error::NamespaceExecutionError;

/// Write-once completion cell: the single internal "done?" truth, backed by a
/// `Mutex` + `Condvar`. Single-consumer — exactly one `wait` takes the value.
pub struct CompletionPromise<T> {
    slot: Mutex<Option<Result<T, NamespaceExecutionError>>>,
    ready: Condvar,
}

impl<T> Default for CompletionPromise<T> {
    fn default() -> Self {
        Self::new()
    }
}

impl<T> CompletionPromise<T> {
    pub fn new() -> Self {
        Self {
            slot: Mutex::new(None),
            ready: Condvar::new(),
        }
    }

    /// Pending → resolved, then `notify_all`. Returns `false` if already resolved.
    pub fn resolve(&self, outcome: Result<T, NamespaceExecutionError>) -> bool {
        let mut slot = self.slot.lock().expect("completion promise mutex poisoned");
        if slot.is_none() {
            *slot = Some(outcome);
            self.ready.notify_all();
            true
        } else {
            false
        }
    }

    pub fn is_resolved(&self) -> bool {
        let slot = self.slot.lock().expect("completion promise mutex poisoned");
        slot.is_some()
    }

    /// Block until resolved, then take the value (single-consumer).
    pub fn wait(&self) -> Result<T, NamespaceExecutionError> {
        let mut slot = self.slot.lock().expect("completion promise mutex poisoned");
        while slot.is_none() {
            slot = self
                .ready
                .wait(slot)
                .expect("completion promise mutex poisoned");
        }
        slot.take()
            .expect("wait loop exits only once the slot is resolved")
    }
}
