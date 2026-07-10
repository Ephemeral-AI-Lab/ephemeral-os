use std::path::PathBuf;
use std::sync::{Mutex, MutexGuard};

use crate::error::WorkspaceError;
use crate::session::WorkspaceManager;

mod hooks;
mod impls;
mod support;

pub use hooks::WorkspaceRuntimeHooks;

pub struct WorkspaceRuntimeService {
    backend: WorkspaceRuntimeBackend,
}

pub(crate) struct WorkspaceRuntimeState {
    pub(crate) manager: WorkspaceManager,
    pub(crate) layer_stack_root: PathBuf,
}

enum WorkspaceRuntimeBackend {
    Runtime(Box<Mutex<WorkspaceRuntimeState>>),
    Hooks(WorkspaceRuntimeHooks),
}

impl WorkspaceRuntimeService {
    #[must_use]
    pub fn new(manager: WorkspaceManager, layer_stack_root: PathBuf) -> Self {
        Self {
            backend: WorkspaceRuntimeBackend::Runtime(Box::new(Mutex::new(
                WorkspaceRuntimeState {
                    manager,
                    layer_stack_root,
                },
            ))),
        }
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

    /// The isolated-network IP of a mounted workspace session, when it has one.
    /// Reads live session state; shared or veth-less workspaces yield `None`.
    ///
    /// # Errors
    /// Returns an error when the runtime state lock cannot be taken.
    pub fn isolated_ip(
        &self,
        workspace_id: &crate::model::WorkspaceSessionId,
    ) -> Result<Option<std::net::Ipv4Addr>, WorkspaceError> {
        match &self.backend {
            WorkspaceRuntimeBackend::Runtime(_) => {
                Ok(self.lock_state()?.manager.isolated_ip(workspace_id))
            }
            WorkspaceRuntimeBackend::Hooks(hooks) => (hooks.isolated_ip)(workspace_id),
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
