use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::Path;
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::error::LayerStackError;
use crate::fs::{canonical_key, next_unique};
use crate::model::{LayerRef, Manifest};

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct LayerStackLeaseRecord {
    pub(super) lease_id: String,
    pub(super) manifest: Manifest,
}

#[derive(Debug, Default)]
pub(super) struct LeaseRegistry {
    leases: HashMap<String, LayerStackLeaseRecord>,
    refcounts: BTreeMap<LayerRef, usize>,
}

pub(super) type SharedLeaseRegistry = Arc<Mutex<LeaseRegistry>>;

pub(super) fn shared_registry_for_root(
    storage_root: &Path,
) -> Result<SharedLeaseRegistry, LayerStackError> {
    let key = canonical_key(storage_root);
    let mut registries = shared_registries()
        .lock()
        .map_err(|_| LayerStackError::LockPoisoned("lease registry map"))?;
    Ok(registries
        .entry(key)
        .or_insert_with(|| Arc::new(Mutex::new(LeaseRegistry::default())))
        .clone())
}

pub(super) fn lock_shared_registry(
    registry: &SharedLeaseRegistry,
) -> Result<MutexGuard<'_, LeaseRegistry>, LayerStackError> {
    registry
        .lock()
        .map_err(|_| LayerStackError::LockPoisoned("lease registry"))
}

pub(super) fn lock_shared_registry_recover(
    registry: &SharedLeaseRegistry,
) -> MutexGuard<'_, LeaseRegistry> {
    registry
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

impl LeaseRegistry {
    pub(super) fn acquire(
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
            *self.refcounts.entry(layer.clone()).or_insert(0) += 1;
        }
        self.leases.insert(lease.lease_id.clone(), lease.clone());
        Ok(lease)
    }

    pub(super) fn release(&mut self, lease_id: &str) -> Option<LayerStackLeaseRecord> {
        let lease = self.leases.remove(lease_id)?;
        for layer in &lease.manifest.layers {
            match self.refcounts.get_mut(layer) {
                Some(count) if *count > 1 => *count -= 1,
                Some(_) => {
                    self.refcounts.remove(layer);
                }
                None => {}
            }
        }
        Some(lease)
    }

    pub(super) fn leased_layers(&self) -> Vec<LayerRef> {
        self.refcounts.keys().cloned().collect()
    }

    pub(super) fn lease_head_layers(&self) -> Vec<LayerRef> {
        self.leases
            .values()
            .filter_map(|lease| lease.manifest.layers.first())
            .cloned()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect()
    }

    pub(super) fn active_count(&self) -> usize {
        self.leases.len()
    }
}

fn new_lease_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    format!("{nanos:032x}{:016x}", next_unique())
}

fn shared_registries() -> &'static Mutex<HashMap<String, SharedLeaseRegistry>> {
    static REGISTRIES: OnceLock<Mutex<HashMap<String, SharedLeaseRegistry>>> = OnceLock::new();
    REGISTRIES.get_or_init(|| Mutex::new(HashMap::new()))
}

pub(crate) fn reset_shared_registries_for_tests() {
    shared_registries()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .clear();
}
