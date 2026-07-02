use std::collections::{HashMap, VecDeque};
use std::sync::Mutex;

use crate::error::NamespaceExecutionError;
use crate::shell::NamespaceExecutionTerminalStatus;
use crate::types::NamespaceExecutionId;

/// Default cap on retained terminal entries. Marking an entry terminal evicts
/// the oldest terminal entry beyond the cap, dropping its value (which closes
/// the pty master fd and releases whatever the value's own `Drop` owns). A
/// drain against an evicted id observes a missing entry.
pub const MAX_TERMINAL_ENTRIES: usize = 512;

pub struct ExecutionRegistry<V> {
    inner: Mutex<RegistryState<V>>,
    max_active: usize,
}

struct RegistryState<V> {
    entries: HashMap<NamespaceExecutionId, Entry<V>>,
    active: usize,
    terminal_order: VecDeque<NamespaceExecutionId>,
    max_terminal: usize,
}

struct Entry<V> {
    value: Option<V>,
    terminal: bool,
}

impl<V> Entry<V> {
    const fn reserved() -> Self {
        Self {
            value: None,
            terminal: false,
        }
    }
}

impl<V> ExecutionRegistry<V> {
    #[must_use]
    pub fn new(max_active: usize) -> Self {
        Self {
            inner: Mutex::new(RegistryState {
                entries: HashMap::new(),
                active: 0,
                terminal_order: VecDeque::new(),
                max_terminal: MAX_TERMINAL_ENTRIES,
            }),
            max_active,
        }
    }

    /// Override the terminal-entry retention cap (defaults to
    /// [`MAX_TERMINAL_ENTRIES`]). An over-cap backlog is trimmed on the next
    /// terminal transition, not immediately.
    pub fn set_terminal_retention(&self, max_terminal: usize) {
        self.lock().max_terminal = max_terminal;
    }

    pub fn try_reserve(&self, id: &NamespaceExecutionId) -> Result<(), NamespaceExecutionError> {
        let mut state = self.lock();
        if state.active >= self.max_active {
            return Err(NamespaceExecutionError::Admission {
                max_active: self.max_active,
            });
        }
        state.entries.insert(id.clone(), Entry::reserved());
        state.active += 1;
        Ok(())
    }

    pub fn attach(&self, id: &NamespaceExecutionId, value: V) {
        if let Some(entry) = self.lock().entries.get_mut(id) {
            entry.value = Some(value);
        }
    }

    pub fn abort(&self, id: &NamespaceExecutionId) {
        let mut state = self.lock();
        if let Some(entry) = state.entries.remove(id) {
            if !entry.terminal {
                state.active = state.active.saturating_sub(1);
            }
        }
    }

    pub fn complete(
        &self,
        id: &NamespaceExecutionId,
        _status: NamespaceExecutionTerminalStatus,
        _exit: Option<i64>,
    ) {
        let mut evicted = Vec::new();
        {
            let mut state = self.lock();
            if let Some(entry) = state.entries.get_mut(id) {
                if !entry.terminal {
                    entry.terminal = true;
                    state.active = state.active.saturating_sub(1);
                    state.terminal_order.push_back(id.clone());
                    while state.terminal_order.len() > state.max_terminal {
                        let Some(oldest) = state.terminal_order.pop_front() else {
                            break;
                        };
                        if let Some(entry) = state.entries.remove(&oldest) {
                            evicted.push(entry);
                        }
                    }
                }
            }
        }
        drop(evicted);
    }

    pub fn with_value<R>(&self, id: &NamespaceExecutionId, f: impl FnOnce(&V) -> R) -> Option<R> {
        self.lock()
            .entries
            .get(id)
            .and_then(|entry| entry.value.as_ref())
            .map(f)
    }

    #[must_use]
    pub fn is_live(&self, id: &NamespaceExecutionId) -> bool {
        self.lock()
            .entries
            .get(id)
            .is_some_and(|entry| !entry.terminal)
    }

    #[must_use]
    pub fn is_completed(&self, id: &NamespaceExecutionId) -> bool {
        self.lock()
            .entries
            .get(id)
            .is_some_and(|entry| entry.terminal)
    }

    pub fn live_values<R>(&self, f: impl Fn(&V) -> Option<R>) -> Vec<R> {
        self.lock()
            .entries
            .values()
            .filter(|entry| !entry.terminal)
            .filter_map(|entry| entry.value.as_ref())
            .filter_map(f)
            .collect()
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, RegistryState<V>> {
        self.inner
            .lock()
            .expect("execution registry mutex poisoned")
    }
}
