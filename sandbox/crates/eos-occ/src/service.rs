//! OCC service: prepare typed changesets, commit through the single writer,
//! run post-publish maintenance, and the inverted daemon-accessor port.
//!
//! The service routes changes into [`PublishDecision`]s, submits the prepared
//! changeset to the per-root [`CommitQueue`], and (optionally) runs an
//! auto-squash maintenance policy once a publish lands.

use eos_protocol::LayerChange;

use crate::commit_queue::{CommitQueue, CommitTransactionPort, PreparedChangeset};
use crate::error::OccError;
use crate::route::ChangesetResult;

/// Layer depth at which auto-squash maintenance kicks in.
// PORT backend/src/sandbox/occ/service.py:34 — AUTO_SQUASH_MAX_DEPTH = 100
pub const AUTO_SQUASH_MAX_DEPTH: u32 = 100;

/// Post-publish maintenance hook run after a successful OCC commit.
///
/// Mirrors the Python `MaintenancePolicy` Protocol; implementations are
/// synchronous and return per-phase timings.
// PORT backend/src/sandbox/occ/maintenance.py:15 — class MaintenancePolicy(Protocol)
pub trait MaintenancePolicy {
    /// Run maintenance after a publish lands; returns timing keys.
    fn after_publish_sync(&self, result: &ChangesetResult) -> Result<(), OccError>;
}

/// Layer-stack squash capability consumed by [`AutoSquashMaintenancePolicy`].
///
/// Local placeholder for the real port (lives in `eos-layerstack`); the daemon
/// injects a layer-stack-backed implementation.
// PORT backend/src/sandbox/occ/maintenance.py:21 — class _LayerSquashPort(Protocol)
pub trait LayerSquashPort {
    /// Can the active stack be squashed at `max_depth`?
    fn can_squash(&self, max_depth: u32) -> bool;

    /// Squash to `max_depth`; returns the new active manifest version, if any.
    fn squash(&self, max_depth: u32) -> Result<Option<u64>, OccError>;
}

/// Synchronous layer-stack squash after successful publishes.
///
/// Each policy owns its own squash lock (Python `_squash_lock`) so concurrent
/// publishes do not double-squash; it re-reads the active manifest under the
/// lock before deciding.
// PORT backend/src/sandbox/occ/maintenance.py:29 — class AutoSquashMaintenancePolicy
pub struct AutoSquashMaintenancePolicy<S: LayerSquashPort> {
    squasher: S,
    max_depth: u32,
}

impl<S: LayerSquashPort> AutoSquashMaintenancePolicy<S> {
    /// Build a policy that squashes above `max_depth`.
    pub fn new(squasher: S, max_depth: u32) -> Self {
        Self {
            squasher,
            max_depth,
        }
    }
}

impl<S: LayerSquashPort> MaintenancePolicy for AutoSquashMaintenancePolicy<S> {
    // PORT backend/src/sandbox/occ/maintenance.py:44 — after_publish_sync(): depth gate + squash
    fn after_publish_sync(&self, result: &ChangesetResult) -> Result<(), OccError> {
        let _ = (&self.squasher, self.max_depth, result);
        todo!("PORT occ/maintenance.py:44 — gate on published version + active depth, then squash")
    }
}

/// Prepare typed OCC changesets and commit them through the single writer.
///
/// Holds the per-root [`CommitQueue`] and an optional maintenance policy. There
/// is exactly one `OccService` per `layer_stack_root` (the MF-1 owner).
pub struct OccService<T: CommitTransactionPort + 'static> {
    commit_queue: CommitQueue<T>,
}

impl<T: CommitTransactionPort + 'static> OccService<T> {
    /// Build a service over an already-started commit queue.
    pub fn new(commit_queue: CommitQueue<T>) -> Self {
        Self { commit_queue }
    }

    /// Prepare and commit a changeset through the layer stack.
    // PORT backend/src/sandbox/occ/service.py:63 — apply_changeset()
    pub fn apply_changeset(
        &self,
        changes: &[LayerChange],
        snapshot_version: Option<u64>,
        atomic: bool,
    ) -> Result<ChangesetResult, OccError> {
        let _ = (&self.commit_queue, changes, snapshot_version, atomic);
        todo!("PORT occ/service.py:63 — prepare_changeset then commit_prepared")
    }

    /// Route raw changes into a [`PreparedChangeset`] (Drop/Direct/Gated/Reject).
    // PORT backend/src/sandbox/occ/service.py:230 — prepare_changeset()
    pub fn prepare_changeset(
        &self,
        changes: &[LayerChange],
        snapshot_version: Option<u64>,
        atomic: bool,
    ) -> Result<PreparedChangeset, OccError> {
        let _ = (changes, snapshot_version, atomic);
        todo!("PORT occ/service.py:230 — classify routes + compute changeset_id")
    }
}

/// Inverted daemon accessor: the OCC runtime-services bundle, keyed per root.
///
/// `eos-occ` (a lower crate) defines this PORT; `eos-daemon` implements and
/// injects it so the upward Python edge (`daemon.occ_runtime_services` imported
/// by ephemeral/isolated) becomes a leaf→root trait dependency. The single
/// per-root services instance is the MF-1 owner of the one `occ-commit-queue`
/// writer — implementations MUST return the same bundle (and thus the same
/// queue + storage lease) for a given `layer_stack_root`, never a second
/// writer.
// PORT backend/src/sandbox/daemon/occ_runtime_services.py:48 — get_occ_runtime_services(layer_stack_root)
pub trait OccRuntimeServicesPort {
    /// Concrete commit-transaction implementation the queue drives.
    type Transaction: CommitTransactionPort + 'static;

    /// Return the daemon-local OCC service for `layer_stack_root`.
    ///
    /// Cached per root (LRU, max 256) so the single writer is reused.
    // PORT backend/src/sandbox/daemon/occ_runtime_services.py:48 — per-root LRU cache
    fn occ_runtime_services(
        &self,
        layer_stack_root: &str,
    ) -> Result<&OccService<Self::Transaction>, OccError>;
}
