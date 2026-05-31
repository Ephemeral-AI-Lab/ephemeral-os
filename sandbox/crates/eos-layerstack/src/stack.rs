//! The `LayerStack` storage facade, its merged read view, and the snapshot
//! lease value type.
//!
//! `LayerStack` coordinates the SINGLE linearization point: one mutable
//! `manifest.json` over immutable content-addressed layer directories, swapped
//! atomically. A snapshot is O(1) — it acquires a lease and returns the
//! EXISTING `layer_paths`, NEVER a rendered tree (rendering is the caller's
//! overlay/projection concern).
//! `// PORT backend/src/sandbox/layer_stack/stack.py:73-393 — LayerStack`
//! `// PORT backend/src/sandbox/layer_stack/view.py:44 — MergedView`

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use eos_protocol::{LayerRef, Manifest};

use crate::error::LayerStackError;
use crate::lease::LeaseRegistry;
use crate::storage_lock::StorageWriterLockLease;

/// Immutable result of an O(1) snapshot: a lease id + the pinned manifest's
/// existing on-disk layer paths. NEVER a rendered tree.
/// `// PORT backend/src/sandbox/layer_stack/stack.py:52-70 — LayerStackSnapshotLease`
// No `Eq`: `timings` holds `f64` (no total ordering).
#[derive(Debug, Clone, PartialEq)]
pub struct Lease {
    pub lease_id: String,
    pub manifest_version: i64,
    pub root_hash: String,
    pub manifest: Manifest,
    /// POSIX paths of the manifest's layer directories, in manifest order.
    pub layer_paths: Vec<String>,
    /// Phase timings keyed `layer_stack.acquire_snapshot.*`.
    pub timings: BTreeMap<String, f64>,
}

/// Layered read view over a storage root's manifest (lowest→highest precedence).
/// Reads resolve through the manifest's layer directories without materializing
/// a tree; this is the pure-read sibling of the overlay mount.
/// `// PORT backend/src/sandbox/layer_stack/view.py:44-* — MergedView`
#[derive(Debug)]
pub struct MergedView {
    _storage_root: PathBuf,
}

impl MergedView {
    /// Bind a merged view to a storage root.
    /// `// PORT backend/src/sandbox/layer_stack/view.py:45-* — MergedView.__init__`
    pub fn new(storage_root: PathBuf) -> Self {
        Self {
            _storage_root: storage_root,
        }
    }

    /// Read a path's raw bytes through `manifest`. Returns `(bytes, found)`.
    /// `// PORT backend/src/sandbox/layer_stack/view.py:66 — read_bytes`
    pub fn read_bytes(
        &self,
        path: &str,
        manifest: &Manifest,
    ) -> Result<(Option<Vec<u8>>, bool), LayerStackError> {
        let _ = (path, manifest);
        // PORT backend/src/sandbox/layer_stack/view.py:66-78 — resolve through layers newest-first, honor whiteouts
        todo!("PORT: MergedView.read_bytes")
    }

    /// Project the merged view of `manifest` into `destination` (full render).
    /// `// PORT backend/src/sandbox/layer_stack/view.py:195 — project`
    pub fn project(&self, destination: &Path, manifest: &Manifest) -> Result<(), LayerStackError> {
        let _ = (destination, manifest);
        // PORT backend/src/sandbox/layer_stack/view.py:195-* — materialize merged tree at destination
        todo!("PORT: MergedView.project")
    }
}

/// Durable storage facade for one layer-stack root.
///
/// Owns the manifest pointer, the lease registry, the merged read view, the
/// publisher, and the squasher. Holds the dual-layer storage-writer lease for
/// its lifetime (acquired in [`LayerStack::open`]).
/// `// PORT backend/src/sandbox/layer_stack/stack.py:73-96 — LayerStack.__init__`
#[derive(Debug)]
pub struct LayerStack {
    storage_root: PathBuf,
    _writer_lock: StorageWriterLockLease,
    _leases: LeaseRegistry,
    _view: MergedView,
}

impl LayerStack {
    /// Open (creating dirs as needed) a layer stack at `storage_root`, acquiring
    /// the cross-process writer lease and seeding an empty manifest if absent.
    /// `// PORT backend/src/sandbox/layer_stack/stack.py:76-96 — __init__`
    pub fn open(storage_root: PathBuf) -> Result<Self, LayerStackError> {
        // PORT backend/src/sandbox/layer_stack/stack.py:80-96 — mkdir storage/layers/staging, acquire writer lock, seed empty manifest
        let writer_lock = StorageWriterLockLease::acquire(&storage_root)?;
        let view = MergedView::new(storage_root.clone());
        Ok(Self {
            storage_root,
            _writer_lock: writer_lock,
            _leases: LeaseRegistry::new(),
            _view: view,
        })
    }

    /// The storage root this stack manages.
    pub fn storage_root(&self) -> &Path {
        &self.storage_root
    }

    /// Read the current active manifest from `manifest.json`.
    /// `// PORT backend/src/sandbox/layer_stack/stack.py:98-99 — read_active_manifest`
    pub fn read_active_manifest(&self) -> Result<Manifest, LayerStackError> {
        // PORT backend/src/sandbox/layer_stack/stack.py:99 — read_manifest(self._manifest_file)
        todo!("PORT: LayerStack.read_active_manifest")
    }

    /// O(1) snapshot: acquire a lease over the active manifest and return its
    /// existing layer paths. NEVER renders a tree.
    /// `// PORT backend/src/sandbox/layer_stack/stack.py:108-135 — acquire_snapshot`
    pub fn acquire_snapshot(&mut self, owner_request_id: &str) -> Result<Lease, LayerStackError> {
        let _ = owner_request_id;
        // PORT backend/src/sandbox/layer_stack/stack.py:108-135 — leases.acquire(manifest), map layer paths, root_hash, timings
        todo!("PORT: LayerStack.acquire_snapshot")
    }

    /// Release a snapshot lease by id and GC any now-unreferenced layers.
    /// Returns `false` if the lease id was unknown.
    /// `// PORT backend/src/sandbox/layer_stack/stack.py:137-149 — release_lease`
    pub fn release_lease(&mut self, lease_id: &str) -> Result<bool, LayerStackError> {
        let _ = lease_id;
        // PORT backend/src/sandbox/layer_stack/stack.py:137-149 — under write guard: release, compute unreferenced, remove layers
        todo!("PORT: LayerStack.release_lease")
    }

    /// Whether a squash would reduce manifest depth below `max_depth`.
    /// `// PORT backend/src/sandbox/layer_stack/stack.py:157-168 — can_squash`
    pub fn can_squash(&self, max_depth: usize) -> Result<bool, LayerStackError> {
        let _ = max_depth;
        // PORT backend/src/sandbox/layer_stack/stack.py:157-168 — squasher.plan(..., lease_head_layers, min_reduction=2) is Some
        todo!("PORT: LayerStack.can_squash")
    }

    /// Non-destructively squash foldable runs, swapping a shorter manifest.
    /// Returns the new manifest, or `None` if nothing was foldable.
    /// `// PORT backend/src/sandbox/layer_stack/stack.py:236-298 — squash`
    pub fn squash(&mut self, max_depth: usize) -> Result<Option<Manifest>, LayerStackError> {
        let _ = max_depth;
        // PORT backend/src/sandbox/layer_stack/squash.py:179 — checkpoint id "B{next_version:06}-{uuid8}"
        // PORT backend/src/sandbox/layer_stack/stack.py:236-298 — plan, build checkpoints, atomic pointer-swap, rollback in finally
        todo!("PORT: LayerStack.squash")
    }

    /// Full retention keep-set (GC). DISTINCT from squash barriers.
    /// `// PORT backend/src/sandbox/layer_stack/stack.py:151-152 — leased_layers`
    pub fn leased_layers(&self) -> Vec<LayerRef> {
        // PORT backend/src/sandbox/layer_stack/lease.py:57-66 — leases.leased_layers()
        todo!("PORT: LayerStack.leased_layers")
    }

    /// Squash-keep barrier set. DISTINCT from the GC retention set.
    /// `// PORT backend/src/sandbox/layer_stack/lease.py:68-85 — lease_head_layers`
    pub fn lease_head_layers(&self) -> Vec<LayerRef> {
        // PORT backend/src/sandbox/layer_stack/lease.py:68-85 — leases.lease_head_layers()
        todo!("PORT: LayerStack.lease_head_layers")
    }
}
