use std::sync::{Condvar, Mutex};
use std::time::Duration;

use crate::error::NamespaceExecutionError;

pub trait CompletionWaiter: Send + Sync {
    fn wait_timeout(&self, timeout: Duration) -> bool;
}

impl<T: Send> CompletionWaiter for CompletionPromise<T> {
    fn wait_timeout(&self, timeout: Duration) -> bool {
        CompletionPromise::wait_timeout(self, timeout)
    }
}

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

    pub fn wait(&self) -> Result<T, NamespaceExecutionError> {
        let mut slot = self
            .ready
            .wait_while(
                self.slot.lock().expect("completion promise mutex poisoned"),
                |slot| slot.is_none(),
            )
            .expect("completion promise mutex poisoned");
        slot.take()
            .expect("wait loop exits only once the slot is resolved")
    }

    pub fn wait_timeout(&self, timeout: Duration) -> bool {
        let slot = self.slot.lock().expect("completion promise mutex poisoned");
        self.ready
            .wait_timeout_while(slot, timeout, |slot| slot.is_none())
            .expect("completion promise mutex poisoned")
            .0
            .is_some()
    }

    pub fn resolved(&self) -> Option<Result<T, NamespaceExecutionError>>
    where
        T: Clone,
    {
        let slot = self.slot.lock().expect("completion promise mutex poisoned");
        slot.clone()
    }
}
