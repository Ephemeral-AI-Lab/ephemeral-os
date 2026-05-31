//! Exact layer-ref lease registry for frozen layer-stack snapshots.
//!
//! Owns the DUAL-SET distinction that the GC and squash paths depend on:
//!
//! - [`LeaseRegistry::leased_layers`] — the FULL on-disk retention set: every
//!   layer referenced by at least one active lease's frozen manifest. GC must
//!   keep these directories on disk until the lease releases.
//! - [`LeaseRegistry::lease_head_layers`] — the SQUASH-KEEP barrier set: only
//!   the NEWEST layer of each active lease's manifest. Layers below a head are
//!   foldable; the lease still reads through its own frozen manifest via the
//!   retention set above. These two sets are DISTINCT and must not be conflated.
//!
//! `// PORT backend/src/sandbox/layer_stack/lease.py`

use eos_protocol::{LayerRef, Manifest};

/// One active snapshot lease: an id bound to the frozen manifest it pins.
/// `// PORT backend/src/sandbox/layer_stack/lease.py:14-17 — LayerStackLeaseRecord`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LayerStackLeaseRecord {
    pub lease_id: String,
    pub manifest: Manifest,
}

/// Tracks active snapshot leases and the layers they retain on disk.
///
/// Python guards this with a `threading.RLock` and a `Counter[LayerRef]`
/// refcount; the Rust port keeps the same refcount semantics.
/// `// PORT backend/src/sandbox/layer_stack/lease.py:20-31 — LeaseRegistry`
#[derive(Debug, Default)]
pub struct LeaseRegistry {
    _private: (),
}

impl LeaseRegistry {
    /// Create an empty registry.
    pub fn new() -> Self {
        Self { _private: () }
    }

    /// Register a new lease over `manifest`, owned by `owner_request_id`,
    /// incrementing the per-layer refcount. Rejects an empty owner id.
    /// `// PORT backend/src/sandbox/layer_stack/lease.py:33-47 — acquire`
    pub fn acquire(&mut self, manifest: Manifest, owner_request_id: &str) -> LayerStackLeaseRecord {
        let _ = (manifest, owner_request_id);
        // PORT backend/src/sandbox/layer_stack/lease.py:33-47 — mint lease_id, store record, refcount.update(manifest.layers)
        todo!("PORT: LeaseRegistry.acquire")
    }

    /// Release a lease by id, decrementing per-layer refcounts. Returns the
    /// released record, or `None` if the id was unknown.
    /// `// PORT backend/src/sandbox/layer_stack/lease.py:49-55 — release`
    pub fn release(&mut self, lease_id: &str) -> Option<LayerStackLeaseRecord> {
        let _ = lease_id;
        // PORT backend/src/sandbox/layer_stack/lease.py:49-55 — pop lease, refcount -= Counter(layers)
        todo!("PORT: LeaseRegistry.release")
    }

    /// FULL on-disk retention set (sorted): every layer pinned by an active
    /// lease. This is the GC keep-set. DISTINCT from [`Self::lease_head_layers`].
    /// `// PORT backend/src/sandbox/layer_stack/lease.py:57-66 — leased_layers`
    pub fn leased_layers(&self) -> Vec<LayerRef> {
        // PORT backend/src/sandbox/layer_stack/lease.py:65-66 — sorted(self._refcounts) keys
        todo!("PORT: LeaseRegistry.leased_layers — full retention set")
    }

    /// SQUASH-KEEP barrier set (sorted): the newest layer of each active
    /// lease's manifest. DISTINCT from (a subset of) [`Self::leased_layers`].
    /// `// PORT backend/src/sandbox/layer_stack/lease.py:68-85 — lease_head_layers`
    pub fn lease_head_layers(&self) -> Vec<LayerRef> {
        // PORT backend/src/sandbox/layer_stack/lease.py:76-85 — sorted({lease.manifest.layers[0]})
        todo!("PORT: LeaseRegistry.lease_head_layers — squash barrier set")
    }

    /// Number of active leases.
    /// `// PORT backend/src/sandbox/layer_stack/lease.py:87-89 — active_count`
    pub fn active_count(&self) -> usize {
        // PORT backend/src/sandbox/layer_stack/lease.py:88-89 — len(self._leases)
        todo!("PORT: LeaseRegistry.active_count")
    }
}
