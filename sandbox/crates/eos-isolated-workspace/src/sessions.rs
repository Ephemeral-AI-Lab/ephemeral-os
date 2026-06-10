//! The persistent private session registry: enter/exit lifecycle.
//!
//! [`IsolatedSessions`] owns the per-caller persistent workspaces. `enter`
//! receives an already-acquired snapshot (plain fields — this crate never
//! touches the layer stack), allocates scratch (upper/work), wires the
//! namespace (ns-holder spawn -> ns FDs -> overlay mount -> DNS -> net-ready),
//! and persists the handle. `exit` tears down the namespace + network +
//! cgroup, DISCARDS the upperdir, and returns the `lease_id` so the caller can
//! release the lease it acquired at enter.

use std::collections::HashMap;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::caps::ResourceCaps;
use crate::error::IsolatedError;
use crate::namespace::NamespaceRuntime;
use crate::network::IsolatedNetwork;

use self::resources::monotonic_seconds;

/// Canonical scratch root for isolated workspace manager state and private dirs.
pub(crate) const DEFAULT_ISOLATED_SCRATCH_ROOT: &str = "/eos/scratch/isolated";

mod capacity;
mod gc;
mod handle;
mod lifecycle;
mod persistence;
mod resources;
#[cfg(test)]
mod tests;

pub use handle::WorkspaceHandle;
pub use lifecycle::ExitOutcome;

/// Stable identifier of one isolated workspace (also the scratch-dir / veth
/// name seed).
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct IsolatedWorkspaceId(pub String);

/// The already-acquired snapshot an isolated workspace is entered against.
///
/// Plain fields, deliberately: lease custody stays with the caller; this crate
/// only records the values a handle needs (frozen lower layers, version/hash
/// metadata, and the `lease_id` it hands back at exit).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IsolatedSnapshot {
    pub lease_id: String,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
    pub layer_paths: Vec<PathBuf>,
}

/// Owns the isolated-workspace lifecycle, namespace runtime, capacity, TTL, GC.
///
/// Concrete on purpose: the namespace envelope is this crate's own code (with
/// a stub switch for harness/unit-test runs), the network state is local, and
/// storage never appears.
pub struct IsolatedSessions {
    caps: ResourceCaps,
    runtime: NamespaceRuntime,
    network: IsolatedNetwork,
    scratch_root: PathBuf,
    handles: HashMap<IsolatedWorkspaceId, WorkspaceHandle>,
    by_caller: HashMap<String, IsolatedWorkspaceId>,
}

impl IsolatedSessions {
    /// Construct a session registry with the canonical scratch root.
    ///
    /// The namespace runtime stubs itself when the
    /// `EOS_ISOLATED_WORKSPACE_TEST_HARNESS` env var is `true` (the e2e
    /// harness switch the daemon honored before this crate existed).
    #[must_use]
    pub fn new(caps: ResourceCaps) -> Self {
        Self::with_scratch_root(caps, PathBuf::from(DEFAULT_ISOLATED_SCRATCH_ROOT))
    }

    /// Construct a session registry with an explicit scratch root.
    #[must_use]
    pub fn with_scratch_root(caps: ResourceCaps, scratch_root: PathBuf) -> Self {
        Self::with_runtime(caps, scratch_root, NamespaceRuntime::from_env())
    }

    /// Construct with a fully stubbed namespace runtime (unit tests).
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

    /// Reconcile persisted handles + IP pool at startup before serving enters.
    ///
    /// Returns the orphaned `lease_id`s found in persisted rows; the caller
    /// (which owns the layer stack) releases them.
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError`] when the feature is disabled, network setup
    /// fails, or the session scratch root cannot be created.
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

    /// Return a copy of the active handle for `caller_id`, if any.
    #[must_use]
    pub fn get_handle(&self, caller_id: &str) -> Option<WorkspaceHandle> {
        self.by_caller
            .get(caller_id)
            .and_then(|workspace_id| self.handles.get(workspace_id))
            .cloned()
    }

    /// Return every caller with an open handle.
    #[must_use]
    pub fn list_open_callers(&self) -> Vec<String> {
        self.by_caller.keys().cloned().collect()
    }

    /// Bump `caller_id`'s last-activity time (the TTL liveness input).
    ///
    /// File and command operations scoped to the workspace call this so an
    /// actively used handle is not TTL-evicted.
    pub fn touch(&mut self, caller_id: &str) {
        if let Some(handle) = self
            .by_caller
            .get(caller_id)
            .and_then(|workspace_id| self.handles.get_mut(workspace_id))
        {
            handle.last_activity = monotonic_seconds();
        }
    }

    /// Sweep naming-convention resources that no longer have persisted rows.
    ///
    /// Test reset and daemon startup call this before accepting new handles.
    /// On a fresh daemon there are no live handles, so every `eos-iws-*`
    /// resource left in the host namespace is an orphan candidate.
    pub fn reap_orphan_resources(&mut self) {
        self.reap_named_orphans();
    }
}
