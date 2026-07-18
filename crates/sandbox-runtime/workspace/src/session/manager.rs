//! Workspace manager.
//!
//! The manager owns admission policy, persistence, and the lifecycle
//! modules own network-mode-specific setup, shared holder, overlay, teardown,
//! and persistence behavior.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use sandbox_observability_telemetry::Observer;
use serde::Deserialize;

use crate::isolated_network_setup::IsolatedNetwork;
use crate::lifecycle::destroy::TeardownTransaction;
use crate::model::{WorkspaceOwnershipSnapshot, WorkspaceSessionId};
use crate::namespace::NamespaceRuntime;
pub use crate::session::{HolderNsFds, MountedWorkspace};

pub use crate::lifecycle::ExitOutcome;

pub(crate) const PERSISTED_HANDLES_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Rfc1918Egress {
    Allow,
    Deny,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ResourceCaps {
    pub setup_timeout_s: f64,
    pub exit_grace_s: f64,
    pub rfc1918_egress: Rfc1918Egress,
    /// Freeze-poll budget for the remount quiesce, in seconds.
    pub freeze_budget_s: f64,
}

impl Default for ResourceCaps {
    fn default() -> Self {
        Self {
            setup_timeout_s: 30.0,
            exit_grace_s: 0.25,
            rfc1918_egress: Rfc1918Egress::Allow,
            freeze_budget_s: 0.5,
        }
    }
}

#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum WorkspaceManagerError {
    #[error("invalid argument: {0}")]
    InvalidArgument(String),

    #[error("workspace session is not open")]
    NotOpen,

    #[error("workspace session is already open: {workspace_session_id:?}")]
    AlreadyOpen {
        workspace_session_id: WorkspaceSessionId,
    },

    #[error("setup failed at step {step}")]
    SetupFailed { step: String },

    #[error("isolated network unavailable: {0}")]
    NetworkUnavailable(String),

    #[error("workspace teardown remains retryable for {workspace_session_id:?}: {failures:?}")]
    TeardownFailed {
        workspace_session_id: WorkspaceSessionId,
        failures: Vec<String>,
    },
}

pub struct WorkspaceManager {
    pub(crate) workspace_root: String,
    pub(crate) caps: ResourceCaps,
    pub(crate) runtime: Arc<NamespaceRuntime>,
    pub(crate) network: IsolatedNetwork,
    pub(crate) scratch_root: PathBuf,
    /// Bound by [`crate::WorkspaceRuntimeService`] before the manager can
    /// create or destroy a workspace. Keeping the root on the teardown owner
    /// lets lease release participate in the same retryable transaction as
    /// holder, fd, mount, scratch, and persisted-handle cleanup.
    pub(crate) layer_stack_root: Option<PathBuf>,
    pub(crate) handles: HashMap<WorkspaceSessionId, MountedWorkspace>,
    pub(crate) teardowns: HashMap<WorkspaceSessionId, TeardownTransaction>,
}

impl WorkspaceManager {
    #[must_use]
    pub fn new(
        workspace_root: impl Into<String>,
        caps: ResourceCaps,
        scratch_root: PathBuf,
        obs: Observer,
    ) -> Self {
        let runtime = NamespaceRuntime::new(caps.setup_timeout_s, obs);
        Self::with_runtime(workspace_root, caps, scratch_root, runtime)
    }

    pub(crate) fn with_runtime(
        workspace_root: impl Into<String>,
        caps: ResourceCaps,
        scratch_root: PathBuf,
        runtime: NamespaceRuntime,
    ) -> Self {
        let network = IsolatedNetwork::new(caps.rfc1918_egress);
        Self {
            workspace_root: workspace_root.into(),
            caps,
            runtime: Arc::new(runtime),
            network,
            scratch_root,
            layer_stack_root: None,
            handles: HashMap::new(),
            teardowns: HashMap::new(),
        }
    }

    pub(crate) fn bind_layer_stack_root(&mut self, layer_stack_root: PathBuf) {
        self.layer_stack_root = Some(layer_stack_root);
    }

    pub(crate) fn take_holder_exit_subscription(
        &self,
    ) -> Result<crate::service::HolderExitSubscription, String> {
        self.runtime.take_holder_exit_subscription()
    }

    pub(crate) fn handle(&self, workspace_id: &WorkspaceSessionId) -> Option<&MountedWorkspace> {
        self.handles.get(workspace_id)
    }

    pub(crate) fn ensure_workspace_available(
        &self,
        workspace_id: &WorkspaceSessionId,
    ) -> Result<(), WorkspaceManagerError> {
        if self.handles.contains_key(workspace_id) || self.teardowns.contains_key(workspace_id) {
            return Err(WorkspaceManagerError::AlreadyOpen {
                workspace_session_id: workspace_id.clone(),
            });
        }
        Ok(())
    }

    pub(crate) fn workspace_session_root(&self, workspace_id: &WorkspaceSessionId) -> PathBuf {
        self.scratch_root.join(&workspace_id.0)
    }

    pub(crate) fn owned_handles(&self) -> impl Iterator<Item = &MountedWorkspace> {
        self.handles.values().chain(
            self.teardowns
                .values()
                .map(TeardownTransaction::owned_handle),
        )
    }

    pub(crate) fn pending_teardown_ids(&self) -> Vec<WorkspaceSessionId> {
        let mut ids = self.teardowns.keys().cloned().collect::<Vec<_>>();
        ids.sort_by(|left, right| left.0.cmp(&right.0));
        ids
    }

    pub(crate) fn ownership_snapshot(&self) -> WorkspaceOwnershipSnapshot {
        let mut snapshot = WorkspaceOwnershipSnapshot::default();
        for handle in self.owned_handles() {
            snapshot.namespace_fd_count = snapshot
                .namespace_fd_count
                .saturating_add(handle.ns_fds.len());
            snapshot.control_fd_count = snapshot
                .control_fd_count
                .saturating_add(usize::from(handle.readiness_fd >= 0))
                .saturating_add(usize::from(handle.control_fd >= 0));
            snapshot.active_scratch_directories = snapshot
                .active_scratch_directories
                .saturating_add(usize::from(handle.dirs.run_dir.is_dir()));
        }
        snapshot.persisted_workspace_handles = self.handles.len().saturating_add(
            self.teardowns
                .values()
                .filter(|transaction| transaction.has_persisted_handle())
                .count(),
        );
        snapshot
    }

    /// The isolated-network IP of a mounted workspace, when it has one. Shared
    /// workspaces and workspaces without a veth allocation yield `None`.
    #[must_use]
    pub fn isolated_ip(&self, workspace_id: &WorkspaceSessionId) -> Option<std::net::Ipv4Addr> {
        self.handles
            .get(workspace_id)
            .and_then(|workspace| workspace.veth.as_ref())
            .map(|veth| veth.ns_ip)
    }
}

pub(crate) fn validate_workspace_root(workspace_root: &str) -> Result<(), WorkspaceManagerError> {
    let workspace_root = workspace_root.trim();
    if workspace_root.is_empty() {
        return Err(WorkspaceManagerError::InvalidArgument(
            "workspace_root is required".to_owned(),
        ));
    }
    if !Path::new(workspace_root).is_absolute() {
        return Err(WorkspaceManagerError::InvalidArgument(format!(
            "workspace_root must be absolute: {workspace_root}"
        )));
    }
    Ok(())
}
