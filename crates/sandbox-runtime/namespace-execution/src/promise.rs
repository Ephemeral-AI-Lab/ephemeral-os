use std::sync::{Condvar, Mutex};
use std::time::Duration;

use crate::error::NamespaceExecutionError;

/// Write-once completion cell: the single internal "done?" truth, backed by a
/// `Mutex` + `Condvar`. Single-consumer — exactly one `wait` takes the value.
pub(crate) struct CompletionPromise<T> {
    slot: Mutex<Slot<T>>,
    ready: Condvar,
}

enum Slot<T> {
    Pending,
    Ready(Result<T, NamespaceExecutionError>),
    Taken,
}

impl<T> CompletionPromise<T> {
    pub(crate) fn new() -> Self {
        Self {
            slot: Mutex::new(Slot::Pending),
            ready: Condvar::new(),
        }
    }

    /// `Pending` → `Ready`, then `notify_all`. Returns `false` if already resolved.
    pub(crate) fn resolve(&self, outcome: Result<T, NamespaceExecutionError>) -> bool {
        let mut slot = self.slot.lock().expect("completion promise mutex poisoned");
        if matches!(*slot, Slot::Pending) {
            *slot = Slot::Ready(outcome);
            self.ready.notify_all();
            true
        } else {
            false
        }
    }

    pub(crate) fn is_resolved(&self) -> bool {
        let slot = self.slot.lock().expect("completion promise mutex poisoned");
        !matches!(*slot, Slot::Pending)
    }

    /// Block until resolved, then take the value (single-consumer).
    pub(crate) fn wait(&self) -> Result<T, NamespaceExecutionError> {
        let mut slot = self.slot.lock().expect("completion promise mutex poisoned");
        while matches!(*slot, Slot::Pending) {
            slot = self
                .ready
                .wait(slot)
                .expect("completion promise mutex poisoned");
        }
        match std::mem::replace(&mut *slot, Slot::Taken) {
            Slot::Ready(outcome) => outcome,
            Slot::Pending | Slot::Taken => {
                unreachable!("wait loop exits only once the slot is Ready")
            }
        }
    }

    /// Block up to `timeout`; return `is_resolved()` at wake-up.
    pub(crate) fn wait_timeout(&self, timeout: Duration) -> bool {
        let slot = self.slot.lock().expect("completion promise mutex poisoned");
        if !matches!(*slot, Slot::Pending) {
            return true;
        }
        let (slot, _) = self
            .ready
            .wait_timeout(slot, timeout)
            .expect("completion promise mutex poisoned");
        !matches!(*slot, Slot::Pending)
    }
}

#[cfg(test)]
mod tests {
    use super::CompletionPromise;
    use std::time::Duration;

    #[test]
    fn resolve_then_wait_yields_value() {
        let promise = CompletionPromise::<u32>::new();
        assert!(promise.resolve(Ok(42)));
        assert!(promise.is_resolved());
        assert!(!promise.resolve(Ok(7))); // second resolve is rejected
        assert_eq!(promise.wait().expect("resolved Ok"), 42);
    }

    #[test]
    fn wait_timeout_on_pending_returns_false() {
        let promise = CompletionPromise::<u32>::new();
        assert!(!promise.wait_timeout(Duration::from_millis(10)));
        assert!(!promise.is_resolved());
    }
}
