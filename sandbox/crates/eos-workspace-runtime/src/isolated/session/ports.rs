use std::collections::HashMap;
use std::path::PathBuf;

use crate::isolated::error::IsolatedError;

use super::handle::{SnapshotLease, WorkspaceHandle};

/// Snapshot/lease HINGE port — the ONLY layer-stack surface isolated models.
///
/// Defined here as an inverted port (`eos-daemon` injects the layer-stack-backed
/// implementation). It exposes snapshot/lease + read methods ONLY — never the
/// publish-transaction half — so this crate needs neither a direct
/// `eos-layerstack` nor an `eos-occ` dependency.
pub trait LayerStackSnapshotPort {
    /// Acquire a read snapshot + lease for `request_id`.
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError`] when the layer-stack snapshot or lease cannot
    /// be acquired.
    fn acquire_snapshot(&self, request_id: &str) -> Result<SnapshotLease, IsolatedError>;

    /// Release the lease held by `lease_id`. Returns whether it was held.
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError`] when the layer-stack lease release cannot be
    /// checked or completed.
    fn release_lease(&self, lease_id: &str) -> Result<bool, IsolatedError>;

    /// Optional daemon-local diagnostic count for active leases owned by this
    /// port instance. This is intentionally diagnostic-only and exposes no
    /// publish surface.
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError`] when the backing diagnostic state cannot be
    /// inspected.
    fn active_lease_count(&self) -> Result<Option<usize>, IsolatedError> {
        Ok(None)
    }
}

/// Kernel-touching namespace operations the pipeline delegates to.
///
/// Inverted port: the concrete implementation spawns `eosd ns-holder` (the
/// long-lived pidns PID 1) and drives `setns` mounts/exec via `eosd ns-runner`.
/// Both are syscall-only single-threaded crates; this trait keeps the
/// orchestration here free of those edges' details.
pub trait NamespaceRuntimePort {
    /// Spawn `eosd ns-holder` under `unshare(--user --net --pid --mount ...)`,
    /// wait for the `ns-up` handshake token, and return its PID.
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError`] when holder launch or readiness signaling
    /// fails.
    fn spawn_ns_holder(
        &self,
        handle: &mut WorkspaceHandle,
        setup_timeout_s: f64,
    ) -> Result<i32, IsolatedError>;

    /// Open `/proc/<pid>/ns/{user,mnt,pid,net}` FDs for `holder_pid`.
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError`] when namespace FDs cannot be opened.
    fn open_ns_fds(&self, holder_pid: i32) -> Result<HashMap<String, i32>, IsolatedError>;

    /// Mount the overlay inside the namespace (via `eosd ns-runner` setns helper).
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError`] when the setns overlay mount helper fails.
    fn mount_overlay(
        &self,
        handle: &WorkspaceHandle,
        layer_paths: &[PathBuf],
    ) -> Result<(), IsolatedError>;

    /// Configure DNS inside the namespace; returns whether the fallback applied.
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError`] when DNS configuration cannot be applied or
    /// inspected.
    fn configure_dns(
        &self,
        handle: &WorkspaceHandle,
        fallback_dns: &str,
    ) -> Result<bool, IsolatedError>;

    /// Send `net-ready` and await the `ready` token (handshake steps 2-3).
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError`] when the holder control/readiness handshake
    /// fails or times out.
    fn signal_net_ready(
        &self,
        handle: &WorkspaceHandle,
        setup_timeout_s: f64,
    ) -> Result<(), IsolatedError>;

    /// Create the per-workspace cgroup and return its path.
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError`] when cgroup creation fails.
    fn create_cgroup(&self, handle: &WorkspaceHandle) -> Result<PathBuf, IsolatedError>;

    /// SIGTERM (then SIGKILL after `grace_s`) the ns-holder and reap children.
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError`] when holder teardown fails.
    fn kill_holder(&self, holder_pid: i32, grace_s: f64) -> Result<(), IsolatedError>;
}
