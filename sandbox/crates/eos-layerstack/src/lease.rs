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

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

use eos_protocol::{LayerRef, Manifest};

use crate::error::LayerStackError;

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
    leases: HashMap<String, LayerStackLeaseRecord>,
    refcounts: BTreeMap<LayerRefKey, usize>,
}

pub(crate) type SharedLeaseRegistry = Arc<Mutex<LeaseRegistry>>;

pub(crate) fn shared_registry_for_root(
    storage_root: &Path,
) -> Result<SharedLeaseRegistry, LayerStackError> {
    // Leases outlive individual `LayerStack` values: daemon call paths often
    // retain only `lease_id`, then reopen the root later for release/metrics.
    let key = storage_root
        .canonicalize()
        .unwrap_or_else(|_| storage_root.to_path_buf())
        .to_string_lossy()
        .into_owned();
    let mut registries = shared_registries()
        .lock()
        .map_err(|_| LayerStackError::LockPoisoned("lease registry map"))?;
    Ok(registries
        .entry(key)
        .or_insert_with(|| Arc::new(Mutex::new(LeaseRegistry::new())))
        .clone())
}

pub(crate) fn lock_shared_registry(
    registry: &SharedLeaseRegistry,
) -> Result<MutexGuard<'_, LeaseRegistry>, LayerStackError> {
    registry
        .lock()
        .map_err(|_| LayerStackError::LockPoisoned("lease registry"))
}

pub(crate) fn lock_shared_registry_recover(
    registry: &SharedLeaseRegistry,
) -> MutexGuard<'_, LeaseRegistry> {
    registry
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

impl LeaseRegistry {
    /// Create an empty registry.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Register a new lease over `manifest`, owned by `owner_request_id`,
    /// incrementing the per-layer refcount. Rejects an empty owner id.
    ///
    /// # Errors
    ///
    /// Returns [`LayerStackError::InvalidLeaseOwner`] when `owner_request_id` is
    /// empty.
    /// `// PORT backend/src/sandbox/layer_stack/lease.py:33-47 — acquire`
    pub fn acquire(
        &mut self,
        manifest: Manifest,
        owner_request_id: &str,
    ) -> Result<LayerStackLeaseRecord, LayerStackError> {
        if owner_request_id.is_empty() {
            return Err(LayerStackError::InvalidLeaseOwner(
                "owner_request_id must not be empty".to_owned(),
            ));
        }
        let lease = LayerStackLeaseRecord {
            lease_id: new_lease_id(),
            manifest,
        };
        for layer in &lease.manifest.layers {
            *self.refcounts.entry(LayerRefKey::from(layer)).or_insert(0) += 1;
        }
        self.leases.insert(lease.lease_id.clone(), lease.clone());
        Ok(lease)
    }

    /// Release a lease by id, decrementing per-layer refcounts. Returns the
    /// released record, or `None` if the id was unknown.
    /// `// PORT backend/src/sandbox/layer_stack/lease.py:49-55 — release`
    pub fn release(&mut self, lease_id: &str) -> Option<LayerStackLeaseRecord> {
        let lease = self.leases.remove(lease_id)?;
        for layer in &lease.manifest.layers {
            let key = LayerRefKey::from(layer);
            match self.refcounts.get_mut(&key) {
                Some(count) if *count > 1 => *count -= 1,
                Some(_) => {
                    self.refcounts.remove(&key);
                }
                None => {}
            }
        }
        Some(lease)
    }

    /// FULL on-disk retention set (sorted): every layer pinned by an active
    /// lease. This is the GC keep-set. DISTINCT from [`Self::lease_head_layers`].
    /// `// PORT backend/src/sandbox/layer_stack/lease.py:57-66 — leased_layers`
    pub fn leased_layers(&self) -> Vec<LayerRef> {
        self.refcounts.keys().map(LayerRef::from).collect()
    }

    /// SQUASH-KEEP barrier set (sorted): the newest layer of each active
    /// lease's manifest. DISTINCT from (a subset of) [`Self::leased_layers`].
    /// `// PORT backend/src/sandbox/layer_stack/lease.py:68-85 — lease_head_layers`
    pub fn lease_head_layers(&self) -> Vec<LayerRef> {
        self.leases
            .values()
            .filter_map(|lease| lease.manifest.layers.first())
            .map(LayerRefKey::from)
            .collect::<BTreeSet<_>>()
            .iter()
            .map(LayerRef::from)
            .collect()
    }

    /// Number of active leases.
    /// `// PORT backend/src/sandbox/layer_stack/lease.py:87-89 — active_count`
    #[must_use]
    pub fn active_count(&self) -> usize {
        self.leases.len()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct LayerRefKey {
    layer_id: String,
    path: String,
}

impl From<&LayerRef> for LayerRefKey {
    fn from(layer: &LayerRef) -> Self {
        Self {
            layer_id: layer.layer_id.clone(),
            path: layer.path.clone(),
        }
    }
}

impl From<&LayerRefKey> for LayerRef {
    fn from(layer: &LayerRefKey) -> Self {
        Self {
            layer_id: layer.layer_id.clone(),
            path: layer.path.clone(),
        }
    }
}

fn new_lease_id() -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    let counter = COUNTER.fetch_add(1, Ordering::Relaxed);
    format!("{nanos:032x}{counter:016x}")
}

fn shared_registries() -> &'static Mutex<HashMap<String, SharedLeaseRegistry>> {
    static REGISTRIES: OnceLock<Mutex<HashMap<String, SharedLeaseRegistry>>> = OnceLock::new();
    REGISTRIES.get_or_init(|| Mutex::new(HashMap::new()))
}

#[cfg(test)]
mod tests {
    use super::*;

    type TestResult<T = ()> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

    #[test]
    fn acquire_rejects_empty_owner_without_panicking() -> TestResult {
        let manifest = Manifest::new(0, Vec::new(), 1)?;
        let err = match LeaseRegistry::new().acquire(manifest, "") {
            Ok(_) => return Err(std::io::Error::other("empty owner id was accepted").into()),
            Err(error) => error,
        };
        assert!(matches!(err, LayerStackError::InvalidLeaseOwner(_)));
        Ok(())
    }
}
