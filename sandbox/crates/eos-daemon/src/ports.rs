//! Inverted-port IMPLEMENTATIONS — the daemon side of the severings.
//!
//! The lower crates DEFINE port traits (so the crate graph stays leaf->root);
//! `eos-daemon` IMPLEMENTS and injects them here, on daemon-owned injector
//! structs. This is the concrete side of the inversion: every method body is a
//! `todo!()` with a `// PORT` anchor.
//!
//! The injectors implemented here, by severing:
//!
//! * severing #2 — [`OccServicesInjector`] impls BOTH the OCC-side
//!   [`eos_occ::OccRuntimeServicesPort`] (returns the per-root single-writer
//!   [`eos_occ::OccService`]) AND the ephemeral-side
//!   [`eos_ephemeral::OccRuntimeServicesPort`] (applies a changeset through that
//!   ONE writer). These are two DISTINCT traits that happen to share a name; the
//!   daemon owns the per-`layer_stack_root` LRU so both route to the SAME
//!   `occ-commit-queue` writer (MF-1).
//! * severing #3 — [`LayerStackRuntimeInjector`] impls
//!   [`eos_layerstack::LayerStackRuntimePort`] (the daemon-side per-root manager
//!   cache + base construction).
//! * severing #4 — [`ChangesetProjectionInjector`] impls
//!   [`eos_ephemeral::ChangesetProjectionPort`] (projection of published files +
//!   the per-agent dispatch drain-gate).
//!
//! `// PORT backend/src/sandbox/daemon/occ_runtime_services.py:48`
//! `// PORT backend/src/sandbox/daemon/layer_stack_runtime.py`
//! `// PORT backend/src/sandbox/daemon/workspace_tool/changeset_projection.py`
//! `// PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:108-134`

use std::path::Path;

use eos_ephemeral::{
    ChangesetProjectionPort, DispatchSlot, PublishedFile, Result as EphemeralResult,
};
use eos_layerstack::{
    CommitStagingArea, LayerChange, LayerCommitTransaction, LayerStackError, LayerStackRuntimePort,
    Lease, Manifest,
};
use eos_occ::{CommitTransactionPort, OccError, OccService};

/// Daemon-owned OCC runtime-services bundle, keyed per `layer_stack_root`.
///
/// Holds the single [`OccService`] per root (the MF-1 owner of the one
/// `occ-commit-queue` writer). Generic over the concrete commit-transaction the
/// queue drives so the inverted associated/GAT types resolve to a real type.
/// `// PORT backend/src/sandbox/daemon/occ_runtime_services.py:48-90 — get_occ_runtime_services LRU(256)`
pub struct OccServicesInjector<T: CommitTransactionPort + 'static> {
    service: OccService<T>,
}

impl<T: CommitTransactionPort + 'static> OccServicesInjector<T> {
    /// Wrap the per-root [`OccService`] for injection.
    pub fn new(service: OccService<T>) -> Self {
        Self { service }
    }
}

// severing #2 (OCC side): hand back the per-root single-writer service.
impl<T: CommitTransactionPort + 'static> eos_occ::OccRuntimeServicesPort
    for OccServicesInjector<T>
{
    type Transaction = T;

    // PORT backend/src/sandbox/daemon/occ_runtime_services.py:48 — per-root LRU cache -> the ONE OccService
    fn occ_runtime_services(
        &self,
        layer_stack_root: &str,
    ) -> core::result::Result<&OccService<Self::Transaction>, OccError> {
        let _ = (&self.service, layer_stack_root);
        todo!("PORT occ_runtime_services.py:48-90 — resolve per-root services from the LRU(256), single writer per root")
    }
}

// severing #2 (ephemeral side): apply a write/edit changeset through that ONE
// writer and return the published-file results downstream projection consumes.
impl<T: CommitTransactionPort + 'static> eos_ephemeral::OccRuntimeServicesPort
    for OccServicesInjector<T>
{
    // PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:349 — occ_service.apply_changeset via the single per-root writer
    fn apply_changeset(&self, changes: &[LayerChange]) -> EphemeralResult<Vec<PublishedFile>> {
        let _ = (&self.service, changes);
        todo!("PORT workspace_tool/dispatch.py:349 — route through the per-root OccService.apply_changeset, map FileResult -> PublishedFile")
    }
}

/// Daemon-side publish transaction handed out by [`LayerStackRuntimeInjector`].
///
/// Holds the storage-writer guard for its lifetime (the future body owns the
/// dual-layer lease). Named so the [`LayerStackRuntimePort`] GAT resolves to a
/// concrete type rather than a free generic.
/// `// PORT backend/src/sandbox/occ/layer_stack_adapter.py:48-49 — begin_transaction`
pub struct DaemonCommitTransaction<'tx> {
    _root: &'tx Path,
}

impl LayerCommitTransaction for DaemonCommitTransaction<'_> {
    // PORT backend/src/sandbox/occ/ports.py:58 — snapshot (the CAS-expected manifest)
    fn snapshot(&self) -> Manifest {
        let _ = self._root;
        todo!("PORT occ/ports.py:58 — return the manifest snapshotted when the transaction opened")
    }

    // PORT backend/src/sandbox/layer_stack/publisher.py:49-138 — publish_layer (CAS-checked)
    fn publish_layer(
        &mut self,
        changes: &[LayerChange],
        source_root: Option<&Path>,
    ) -> core::result::Result<Manifest, LayerStackError> {
        let _ = (self._root, changes, source_root);
        todo!("PORT publisher.py:49-138 — publish accepted changes as one immutable layer, CAS-checked")
    }
}

/// Daemon-side per-`layer_stack_root` manager cache + base construction.
///
/// severing #3: the lower crates call UP into this accessor; the daemon owns the
/// manager LRU and hands out leases / staging / publish transactions.
/// `// PORT backend/src/sandbox/daemon/layer_stack_runtime.py:37-318`
#[derive(Default)]
pub struct LayerStackRuntimeInjector;

impl LayerStackRuntimePort for LayerStackRuntimeInjector {
    type Transaction<'tx>
        = DaemonCommitTransaction<'tx>
    where
        Self: 'tx;

    // PORT backend/src/sandbox/daemon/layer_stack_runtime.py:137-186 — acquire_snapshot
    fn acquire_snapshot(
        &self,
        layer_stack_root: &Path,
        owner_request_id: &str,
    ) -> core::result::Result<Lease, LayerStackError> {
        let _ = (layer_stack_root, owner_request_id);
        todo!("PORT layer_stack_runtime.py:137-186 — O(1) snapshot lease for a bound, manifest-valid root")
    }

    // PORT backend/src/sandbox/daemon/layer_stack_runtime.py:209-230 — release_lease
    fn release_lease(
        &self,
        layer_stack_root: &Path,
        lease_id: &str,
    ) -> core::result::Result<bool, LayerStackError> {
        let _ = (layer_stack_root, lease_id);
        todo!("PORT layer_stack_runtime.py:209-230 — release a previously-prepared snapshot lease")
    }

    // PORT backend/src/sandbox/occ/layer_stack_adapter.py:51-52 — allocate_commit_staging
    fn allocate_commit_staging(
        &self,
        layer_stack_root: &Path,
        request_id: &str,
    ) -> core::result::Result<CommitStagingArea, LayerStackError> {
        let _ = (layer_stack_root, request_id);
        todo!("PORT layer_stack_adapter.py:51-52 — allocate an OCC-owned staging directory under a root")
    }

    // PORT backend/src/sandbox/occ/layer_stack_adapter.py:48-49 — begin_transaction
    fn begin_transaction(
        &self,
        layer_stack_root: &Path,
    ) -> core::result::Result<Self::Transaction<'_>, LayerStackError> {
        let _ = layer_stack_root;
        todo!("PORT layer_stack_adapter.py:48-49 — open a daemon-owned publish transaction for a root")
    }
}

/// Daemon-side projection of published files + the per-agent dispatch
/// drain-gate.
///
/// severing #4: turns OCC `FileResult`s into `changed_paths`/`conflict`/`status`
/// and owns the short-held entry-lock + inflight bookkeeping that lets
/// `exit_isolated_workspace` quiesce in-flight dispatches. This is the per-agent
/// `AgentQuiesceState` gate — DISTINCT from the invocation-keyed
/// [`crate::in_flight::InFlightRegistry`].
/// `// PORT backend/src/sandbox/daemon/workspace_tool/changeset_projection.py:16-60`
/// `// PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:108-134 — acquire_dispatch_slot`
#[derive(Default)]
pub struct ChangesetProjectionInjector;

impl ChangesetProjectionPort for ChangesetProjectionInjector {
    // PORT backend/src/sandbox/daemon/workspace_tool/changeset_projection.py:16-18 — published_paths
    fn published_paths(&self, files: &[PublishedFile]) -> Vec<String> {
        let _ = files;
        todo!(
            "PORT changeset_projection.py:16-18 — collect paths of every published (success) file"
        )
    }

    // PORT backend/src/sandbox/daemon/workspace_tool/changeset_projection.py:21-38 — conflict_and_status
    fn conflict_and_status(
        &self,
        files: &[PublishedFile],
    ) -> (Option<eos_protocol::ConflictInfo>, String) {
        let _ = files;
        todo!("PORT changeset_projection.py:21-38 — first non-committed file -> (ConflictInfo, status)")
    }

    // PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:108-134 — acquire_dispatch_slot
    fn acquire_dispatch_slot(&self, agent_id: &str) -> EphemeralResult<DispatchSlot> {
        // NB: `DispatchSlot` is `#[non_exhaustive]` in eos-ephemeral, so the
        // guard can only be built inside that crate. This body MUST stay
        // `todo!()`; it is the short-held entry_lock + inflight bump that
        // `exit_isolated_workspace` drains against (raises LifecycleInProgress
        // when exit is pending).
        let _ = agent_id;
        todo!("PORT dispatch.py:108-134 — entry_lock probe exit_pending + inflight++ -> RAII DispatchSlot, decrement on drop")
    }
}
