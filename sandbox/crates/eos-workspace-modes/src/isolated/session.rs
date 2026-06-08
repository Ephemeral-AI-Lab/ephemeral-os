//! The persistent private session: enter/exit lifecycle and control-plane ports.
//!
//! `IsolatedSession` owns the per-caller persistent workspace. `enter` acquires a
//! layer-stack snapshot/lease, allocates scratch (upper/work), wires the
//! namespace (ns-holder spawn -> ns FDs -> overlay mount -> DNS -> net-ready),
//! and persists the handle. `exit` tears down the namespace + network + cgroup,
//! releases the lease, and DISCARDS the upperdir (writes are captured for audit
//! only, never published). The model-facing background-session guard lives in
//! `eos-tools`; daemon enter/exit callers may still run command-session cleanup
//! before mutating lifecycle state.

use std::collections::HashMap;
use std::path::PathBuf;

use crate::isolated::audit::AuditSink;
use crate::isolated::caps::ResourceCaps;
use crate::isolated::error::IsolatedError;
use crate::isolated::network::IsolatedNetwork;
use serde_json::{json, Value};

use self::support::monotonic_seconds;

/// Canonical scratch root for isolated workspace manager state and private dirs.
pub const DEFAULT_ISOLATED_SCRATCH_ROOT: &str = "/eos/scratch/isolated";

mod capacity;
mod gc;
mod lifecycle;
mod persistence;
mod ports;
mod support;
#[cfg(test)]
#[path = "../../tests/session/mod.rs"]
mod tests;
mod types;

pub use ports::{LayerStackSnapshotPort, NamespaceRuntimePort};
pub use types::{CallerId, SnapshotLease, WorkspaceHandle, WorkspaceHandleId};

/// Owns the isolated-workspace lifecycle, namespace runtime, capacity, TTL, GC.
///
/// Generic over the injected snapshot/lease + namespace ports and audit sink so
/// `eos-daemon` wires the kernel-backed implementations and tests inject
/// doubles. Holds the per-caller / per-handle maps and the shared network state.
pub struct IsolatedSession<S, R, A>
where
    S: LayerStackSnapshotPort,
    R: NamespaceRuntimePort,
    A: AuditSink,
{
    caps: ResourceCaps,
    layer_stack: S,
    runtime: R,
    audit: A,
    network: IsolatedNetwork,
    scratch_root: PathBuf,
    handles: HashMap<WorkspaceHandleId, WorkspaceHandle>,
    by_caller: HashMap<CallerId, WorkspaceHandleId>,
}

impl<S, R, A> IsolatedSession<S, R, A>
where
    S: LayerStackSnapshotPort,
    R: NamespaceRuntimePort,
    A: AuditSink,
{
    /// Construct a session with injected ports, caps, and audit sink.
    #[must_use]
    pub fn new(caps: ResourceCaps, layer_stack: S, runtime: R, audit: A) -> Self {
        Self::with_scratch_root(
            caps,
            layer_stack,
            runtime,
            audit,
            PathBuf::from(DEFAULT_ISOLATED_SCRATCH_ROOT),
        )
    }

    /// Construct a session with an explicit scratch root.
    ///
    /// The daemon uses the canonical `/eos/scratch/isolated` root in Docker. Focused
    /// unit tests inject a temporary scratch root through this constructor so
    /// lifecycle behavior can be verified without depending on host `/eos`.
    #[must_use]
    pub fn with_scratch_root(
        caps: ResourceCaps,
        layer_stack: S,
        runtime: R,
        audit: A,
        scratch_root: PathBuf,
    ) -> Self {
        let network = IsolatedNetwork::new(caps.rfc1918_egress);
        Self {
            caps,
            layer_stack,
            runtime,
            audit,
            network,
            scratch_root,
            handles: HashMap::new(),
            by_caller: HashMap::new(),
        }
    }

    /// Reconcile persisted handles + IP pool at startup before serving enters.
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError`] when the feature is disabled, network setup
    /// fails, or the session scratch root cannot be created.
    pub fn initialize(&mut self) -> Result<(), IsolatedError> {
        if !self.caps.enabled {
            return Err(IsolatedError::FeatureDisabled);
        }
        self.network.initialize()?;
        std::fs::create_dir_all(self.session_scratch_root()).map_err(|err| {
            IsolatedError::SetupFailed {
                step: format!("scratch_root: {err}"),
            }
        })?;
        self.reap_startup_orphans()?;
        Ok(())
    }

    /// Return a copy of the active handle for `caller_id`, if any.
    pub fn get_handle(&self, caller_id: &CallerId) -> Option<WorkspaceHandle> {
        self.by_caller
            .get(caller_id)
            .and_then(|workspace_handle_id| self.handles.get(workspace_handle_id))
            .cloned()
    }

    /// Return every caller with an open handle.
    pub fn list_open_callers(&self) -> Vec<String> {
        self.by_caller
            .keys()
            .map(|caller| caller.0.clone())
            .collect()
    }

    /// Emit an isolated tool-call audit event for an active handle.
    pub fn record_tool_call(&mut self, caller_id: &CallerId, mut payload: Value) {
        let Some(workspace_handle_id) = self.by_caller.get(caller_id).cloned() else {
            return;
        };
        let Some(handle) = self.handles.get_mut(&workspace_handle_id) else {
            return;
        };
        handle.last_activity = monotonic_seconds();
        if let Some(object) = payload.as_object_mut() {
            object.insert(
                "workspace_handle_id".to_owned(),
                json!(handle.workspace_handle_id.0),
            );
            object.insert("caller_id".to_owned(), json!(caller_id.0));
        }
        let _ = self
            .audit
            .emit("sandbox_isolated_workspace_tool_call", payload);
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
