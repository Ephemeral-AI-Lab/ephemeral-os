use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Mutex, MutexGuard};

use crate::error::WorkspaceError;
use crate::profile::{ResourceCaps, WorkspaceModeId, WorkspaceModeManager};

mod hooks;
mod impls;
mod support;

pub use hooks::WorkspaceRuntimeHooks;

pub struct WorkspaceRuntimeService {
    backend: WorkspaceRuntimeBackend,
}

pub(crate) struct WorkspaceRuntimeState {
    pub(crate) manager: WorkspaceModeManager,
    pub(crate) layer_stack_roots: HashMap<WorkspaceModeId, PathBuf>,
}

enum WorkspaceRuntimeBackend {
    Runtime(Box<Mutex<WorkspaceRuntimeState>>),
    Hooks(WorkspaceRuntimeHooks),
}

impl WorkspaceRuntimeService {
    #[must_use]
    pub fn new(manager: WorkspaceModeManager) -> Self {
        Self {
            backend: WorkspaceRuntimeBackend::Runtime(Box::new(Mutex::new(
                WorkspaceRuntimeState {
                    manager,
                    layer_stack_roots: HashMap::new(),
                },
            ))),
        }
    }

    #[must_use]
    pub fn with_scratch_root(caps: ResourceCaps, scratch_root: PathBuf) -> Self {
        Self::new(WorkspaceModeManager::with_scratch_root(caps, scratch_root))
    }

    #[doc(hidden)]
    #[must_use]
    pub fn from_hooks_for_test(hooks: WorkspaceRuntimeHooks) -> Self {
        Self {
            backend: WorkspaceRuntimeBackend::Hooks(hooks),
        }
    }

    pub(crate) const fn hooks(&self) -> Option<&WorkspaceRuntimeHooks> {
        match &self.backend {
            WorkspaceRuntimeBackend::Runtime(_) => None,
            WorkspaceRuntimeBackend::Hooks(hooks) => Some(hooks),
        }
    }

    pub(crate) fn lock_state(
        &self,
    ) -> Result<MutexGuard<'_, WorkspaceRuntimeState>, WorkspaceError> {
        match &self.backend {
            WorkspaceRuntimeBackend::Runtime(state) => {
                state.lock().map_err(|_| WorkspaceError::Setup {
                    step: "workspace runtime state lock poisoned".to_owned(),
                })
            }
            WorkspaceRuntimeBackend::Hooks(_) => Err(WorkspaceError::Setup {
                step: "workspace runtime hooks do not expose concrete state".to_owned(),
            }),
        }
    }
}

#[cfg(test)]
#[path = "../tests/unit/service.rs"]
mod tests;
