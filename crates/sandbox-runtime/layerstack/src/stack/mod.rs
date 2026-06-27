use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use crate::error::LayerStackError;
use crate::fs::{read_manifest, resolve_layer_path};
use crate::lock::StorageWriterLockLease;
use crate::model::{manifest_root_hash, Manifest};
use crate::service::{LayerStatus, StackObservation};
use crate::{ACTIVE_MANIFEST_FILE, LAYERS_DIR, STAGING_DIR};

mod layer;
mod lease;
mod ops;
mod projection;
pub mod publish;

use lease::release_lease_locked;
pub(crate) use lease::reset_shared_registries_for_tests;
use lease::{
    lock_shared_registry, lock_shared_registry_recover, shared_registry_for_root, LeaseRegistry,
};

pub use projection::MergedView;

#[derive(Debug, Clone, PartialEq)]
pub struct Lease {
    pub lease_id: String,
    pub manifest: Manifest,
    pub layer_paths: Vec<PathBuf>,
}

impl Lease {
    #[must_use]
    pub fn manifest_version(&self) -> i64 {
        self.manifest.version
    }

    #[must_use]
    pub fn root_hash(&self) -> String {
        manifest_root_hash(&self.manifest)
    }
}

#[derive(Debug)]
pub struct LayerStack {
    pub(in crate::stack) storage_root: PathBuf,
    pub(crate) writer_lock: StorageWriterLockLease,
    pub(in crate::stack) leases: Arc<Mutex<LeaseRegistry>>,
    pub(in crate::stack) view: MergedView,
}

impl LayerStack {
    pub fn open(storage_root: PathBuf) -> Result<Self, LayerStackError> {
        std::fs::create_dir_all(storage_root.join(LAYERS_DIR))?;
        std::fs::create_dir_all(storage_root.join(STAGING_DIR))?;
        let writer_lock = StorageWriterLockLease::acquire(&storage_root)?;
        let leases = shared_registry_for_root(&storage_root)?;
        let view = MergedView::new(storage_root.clone());
        Ok(Self {
            storage_root,
            writer_lock,
            leases,
            view,
        })
    }

    pub fn read_active_manifest(&self) -> Result<Manifest, LayerStackError> {
        let _guard = self.writer_lock.shared()?;
        self.read_active_manifest_unlocked()
    }

    pub(in crate::stack) fn read_active_manifest_unlocked(
        &self,
    ) -> Result<Manifest, LayerStackError> {
        read_manifest(self.storage_root.join(ACTIVE_MANIFEST_FILE))
    }

    pub fn acquire_snapshot(&self, owner_request_id: &str) -> Result<Lease, LayerStackError> {
        let _guard = self.writer_lock.shared()?;
        let manifest = self.read_active_manifest_unlocked()?;
        let lease = {
            let mut leases = lock_shared_registry(&self.leases)?;
            leases.acquire(manifest.clone(), owner_request_id)?
        };
        let layer_paths = manifest
            .layers
            .iter()
            .map(|layer| resolve_layer_path(&self.storage_root, &layer.path))
            .collect();
        Ok(Lease {
            lease_id: lease.lease_id,
            manifest,
            layer_paths,
        })
    }

    pub fn release_lease(&mut self, lease_id: &str) -> Result<bool, LayerStackError> {
        let _guard = self.writer_lock.exclusive()?;
        let mut leases = lock_shared_registry(&self.leases)?;
        release_lease_locked(&self.storage_root, &mut leases, lease_id)
    }

    #[must_use]
    pub fn active_lease_count(&self) -> usize {
        lock_shared_registry_recover(&self.leases).active_count()
    }

    /// Per-layer lease breakdown of the active manifest, base → newest.
    ///
    /// Computed in one pass over the live leases: each layer's
    /// `leased_by_workspaces` is the number of leases whose newest layer is that
    /// layer. The booked-by relation is left to the caller — it is a pure
    /// function of the returned layer order plus the per-layer counts.
    pub fn observe(&self) -> Result<StackObservation, LayerStackError> {
        let _guard = self.writer_lock.shared()?;
        let manifest = self.read_active_manifest_unlocked()?;
        let (active_lease_count, newest_layers) = {
            let leases = lock_shared_registry(&self.leases)?;
            (leases.active_count(), leases.lease_newest_layers())
        };
        let mut leased_counts: HashMap<&str, usize> = HashMap::new();
        for layer in &newest_layers {
            *leased_counts.entry(layer.layer_id.as_str()).or_insert(0) += 1;
        }
        let layers = manifest
            .layers
            .iter()
            .rev()
            .map(|layer| LayerStatus {
                leased_by_workspaces: leased_counts
                    .get(layer.layer_id.as_str())
                    .copied()
                    .unwrap_or(0),
                layer: layer.clone(),
            })
            .collect();
        Ok(StackObservation {
            manifest_version: manifest.version,
            root_hash: manifest_root_hash(&manifest),
            active_lease_count,
            layers,
        })
    }
}
