//! Inverted port traits — THE HINGE and the deliberate publish/no-publish split.
//!
//! # Why the split lives HERE (not in eos-occ)
//!
//! The Python `LayerStackPortAdapter` (`occ/layer_stack_adapter.py`) bundles
//! BOTH snapshot/lease/read AND publish-transaction methods. If that combined
//! port stayed in `eos-occ`, every consumer of snapshot/lease — `eos-isolated`
//! and `eos-plugin` — would be forced to link `eos-occ`, silently breaking the
//! build-time *no-publish* guarantee (`eos-isolated ⊥ eos-occ`,
//! `eos-plugin ⊥ eos-occ`).
//!
//! So the port is SPLIT into two traits, BOTH owned by `eos-layerstack`:
//!
//! - [`SnapshotLeasePort`] — snapshot / acquire_lease / release_lease + reads
//!   ONLY. This is what plugin consumes directly; isolated mirrors the same
//!   narrow shape behind a daemon-injected port to avoid a direct layerstack
//!   edge. It can NEVER publish. Linking this never drags in occ.
//! - [`LayerCommitTransaction`] — the publish-side transaction (open layer,
//!   `publish_layer`). `eos-occ` and daemon publish paths need this.
//!
//! Keeping these as separate traits is the load-bearing structural fact that
//! makes the no-publish guarantee a *type-level* property.
//!
//! [`LayerStackRuntimePort`] is a THIRD, distinct inversion: the daemon-side
//! per-root manager cache + base construction accessor that lower crates call
//! UP into. `eos-daemon` implements and injects it (severing #3).
//! `// PORT backend/src/sandbox/occ/layer_stack_adapter.py:17-73 — LayerStackPortAdapter (split)`
//! `// PORT backend/src/sandbox/occ/ports.py:49-66 — LayerCommitTransaction`
//! `// PORT backend/src/sandbox/daemon/layer_stack_runtime.py — accessor inversion`

use std::path::Path;

use eos_protocol::{LayerChange, Manifest};

use crate::commit_staging::CommitStagingArea;
use crate::error::LayerStackError;
use crate::stack::Lease;

/// THE HINGE: snapshot / lease / read capability with NO publish surface.
///
/// What `eos-plugin` consumes directly; `eos-isolated` uses an equivalent
/// daemon-injected port to keep its Cargo graph smaller. Implementing or
/// holding this trait can never publish a layer — that is the build-time
/// no-publish guarantee expressed as a type. Deliberately split from
/// [`LayerCommitTransaction`].
/// `// PORT backend/src/sandbox/occ/layer_stack_adapter.py:31-73 — read/snapshot/lease/squash half`
pub trait SnapshotLeasePort {
    /// Read the current active manifest.
    /// `// PORT backend/src/sandbox/occ/layer_stack_adapter.py:31-32 — read_active_manifest`
    fn read_active_manifest(&self) -> Result<Manifest, LayerStackError>;

    /// Read a path's bytes through a (frozen) manifest. Returns `(bytes, found)`.
    /// `// PORT backend/src/sandbox/occ/layer_stack_adapter.py:34-39 — read_bytes`
    fn read_bytes(
        &self,
        path: &str,
        manifest: &Manifest,
    ) -> Result<(Option<Vec<u8>>, bool), LayerStackError>;

    /// O(1) snapshot — acquire a lease + return existing layer paths. No render.
    /// `// PORT backend/src/sandbox/occ/layer_stack_adapter.py:57-64 — acquire_snapshot`
    fn acquire_snapshot(&mut self, request_id: &str) -> Result<Lease, LayerStackError>;

    /// Release a snapshot lease by id; GC unreferenced layers.
    /// `// PORT backend/src/sandbox/occ/layer_stack_adapter.py:66-67 — release_lease`
    fn release_lease(&mut self, lease_id: &str) -> Result<bool, LayerStackError>;

    /// Whether a squash would help at `max_depth`.
    /// `// PORT backend/src/sandbox/occ/layer_stack_adapter.py:69-70 — can_squash`
    fn can_squash(&self, max_depth: usize) -> Result<bool, LayerStackError>;

    /// Non-destructive squash; `None` if nothing foldable.
    /// `// PORT backend/src/sandbox/occ/layer_stack_adapter.py:72-73 — squash`
    fn squash(&mut self, max_depth: usize) -> Result<Option<Manifest>, LayerStackError>;
}

/// The PUBLISH-side transaction. Holds the storage-writer guard for its
/// lifetime; [`publish_layer`](LayerCommitTransaction::publish_layer) is the
/// single layer-publish primitive. Only `eos-occ` + `eos-ephemeral` consume it.
/// `// PORT backend/src/sandbox/occ/ports.py:49-66 — LayerCommitTransaction`
pub trait LayerCommitTransaction {
    /// The manifest snapshotted when the transaction opened (the CAS expected).
    /// `// PORT backend/src/sandbox/occ/ports.py:58 — snapshot`
    fn snapshot(&self) -> Manifest;

    /// Publish accepted changes as one new immutable layer, returning the new
    /// active manifest. CAS-checked against [`snapshot`](Self::snapshot).
    /// `// PORT backend/src/sandbox/layer_stack/publisher.py:49-138 — publish_layer`
    fn publish_layer(
        &mut self,
        changes: &[LayerChange],
        source_root: Option<&Path>,
    ) -> Result<Manifest, LayerStackError>;
}

/// Inverted daemon accessor (severing #3): the per-`layer_stack_root` manager
/// cache + base construction the lower crates call UP into. `eos-daemon`
/// implements + injects this; the lower crates depend only on the trait.
/// `// PORT backend/src/sandbox/daemon/layer_stack_runtime.py:37-318`
pub trait LayerStackRuntimePort {
    /// The publish-side commit transaction this implementation hands out.
    type Transaction<'tx>: LayerCommitTransaction
    where
        Self: 'tx;

    /// O(1) snapshot lease for a bound, manifest-valid root.
    /// `// PORT backend/src/sandbox/daemon/layer_stack_runtime.py:137-186 — acquire_snapshot`
    fn acquire_snapshot(
        &self,
        layer_stack_root: &Path,
        owner_request_id: &str,
    ) -> Result<Lease, LayerStackError>;

    /// Release a previously-prepared snapshot lease.
    /// `// PORT backend/src/sandbox/daemon/layer_stack_runtime.py:209-230 — release_lease`
    fn release_lease(
        &self,
        layer_stack_root: &Path,
        lease_id: &str,
    ) -> Result<bool, LayerStackError>;

    /// Allocate an OCC-owned staging directory under a root.
    /// `// PORT backend/src/sandbox/occ/layer_stack_adapter.py:51-52 — allocate_commit_staging`
    fn allocate_commit_staging(
        &self,
        layer_stack_root: &Path,
        request_id: &str,
    ) -> Result<CommitStagingArea, LayerStackError>;

    /// Open a publish transaction for a root (publish-side; daemon-owned).
    /// `// PORT backend/src/sandbox/occ/layer_stack_adapter.py:48-49 — begin_transaction`
    fn begin_transaction(
        &self,
        layer_stack_root: &Path,
    ) -> Result<Self::Transaction<'_>, LayerStackError>;
}
