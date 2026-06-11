use std::collections::HashMap;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::caps::ResourceCaps;
use crate::error::IsolatedError;
use crate::namespace::NamespaceRuntime;
use crate::network::IsolatedNetwork;

use self::lifecycle::monotonic_seconds;

mod capacity;
mod handle;
mod lifecycle;
mod recovery;
#[cfg(test)]
#[path = "../../tests/unit/sessions.rs"]
mod tests;

pub use handle::WorkspaceHandle;
pub use lifecycle::ExitOutcome;

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct IsolatedWorkspaceId(pub String);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IsolatedSnapshot {
    pub lease_id: String,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
    pub layer_paths: Vec<PathBuf>,
}

pub struct IsolatedManager {
    caps: ResourceCaps,
    runtime: NamespaceRuntime,
    network: IsolatedNetwork,
    scratch_root: PathBuf,
    handles: HashMap<IsolatedWorkspaceId, WorkspaceHandle>,
    by_caller: HashMap<String, IsolatedWorkspaceId>,
}

impl IsolatedManager {
    #[must_use]
    pub fn with_scratch_root(caps: ResourceCaps, scratch_root: PathBuf) -> Self {
        Self::with_runtime(caps, scratch_root, NamespaceRuntime::from_env())
    }

    #[must_use]
    pub fn stubbed(caps: ResourceCaps, scratch_root: PathBuf) -> Self {
        Self::with_runtime(caps, scratch_root, NamespaceRuntime::stubbed())
    }

    fn with_runtime(caps: ResourceCaps, scratch_root: PathBuf, runtime: NamespaceRuntime) -> Self {
        let network = IsolatedNetwork::new(caps.rfc1918_egress);
        Self {
            caps,
            runtime,
            network,
            scratch_root,
            handles: HashMap::new(),
            by_caller: HashMap::new(),
        }
    }

    pub fn initialize(&mut self) -> Result<Vec<String>, IsolatedError> {
        if !self.caps.enabled {
            return Err(IsolatedError::FeatureDisabled);
        }
        self.network.initialize()?;
        std::fs::create_dir_all(self.session_scratch_root()).map_err(|err| {
            IsolatedError::SetupFailed {
                step: format!("scratch_root: {err}"),
            }
        })?;
        self.reap_persisted_orphans()
    }

    #[must_use]
    pub fn get_handle(&self, caller_id: &str) -> Option<WorkspaceHandle> {
        self.by_caller
            .get(caller_id)
            .and_then(|workspace_id| self.handles.get(workspace_id))
            .cloned()
    }

    #[must_use]
    pub fn list_open_callers(&self) -> Vec<String> {
        self.by_caller.keys().cloned().collect()
    }

    pub fn touch(&mut self, caller_id: &str) {
        if let Some(handle) = self
            .by_caller
            .get(caller_id)
            .and_then(|workspace_id| self.handles.get_mut(workspace_id))
        {
            handle.last_activity = monotonic_seconds();
        }
    }

    pub fn reap_orphan_resources(&mut self) {
        self.reap_named_orphans();
    }
}
