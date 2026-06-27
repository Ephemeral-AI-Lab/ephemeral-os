use std::collections::{BTreeSet, HashMap};
use std::path::Path;
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::error::LayerStackError;
use crate::fs::{canonical_key, next_unique};
use crate::model::{LayerRef, Manifest};

#[derive(Debug, Clone, PartialEq, Eq)]
pub(in crate::stack) struct LayerStackLeaseRecord {
    pub(in crate::stack) lease_id: String,
    pub(in crate::stack) manifest: Manifest,
}

#[derive(Debug, Default)]
pub(in crate::stack) struct LeaseRegistry {
    leases: HashMap<String, LayerStackLeaseRecord>,
}

pub(in crate::stack) fn shared_registry_for_root(
    storage_root: &Path,
) -> Result<Arc<Mutex<LeaseRegistry>>, LayerStackError> {
    let key = canonical_key(storage_root);
    let mut registries = shared_registries()
        .lock()
        .map_err(|_| LayerStackError::LockPoisoned("lease registry map"))?;
    Ok(registries
        .entry(key)
        .or_insert_with(|| Arc::new(Mutex::new(LeaseRegistry::default())))
        .clone())
}

pub(in crate::stack) fn lock_shared_registry(
    registry: &Arc<Mutex<LeaseRegistry>>,
) -> Result<MutexGuard<'_, LeaseRegistry>, LayerStackError> {
    registry
        .lock()
        .map_err(|_| LayerStackError::LockPoisoned("lease registry"))
}

pub(in crate::stack) fn lock_shared_registry_recover(
    registry: &Arc<Mutex<LeaseRegistry>>,
) -> MutexGuard<'_, LeaseRegistry> {
    registry
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

impl LeaseRegistry {
    pub(in crate::stack) fn acquire(
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
        self.leases.insert(lease.lease_id.clone(), lease.clone());
        Ok(lease)
    }

    pub(in crate::stack) fn release(&mut self, lease_id: &str) -> Option<LayerStackLeaseRecord> {
        self.leases.remove(lease_id)
    }

    pub(in crate::stack) fn leased_layers(&self) -> Vec<LayerRef> {
        self.leases
            .values()
            .flat_map(|lease| &lease.manifest.layers)
            .cloned()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect()
    }

    pub(in crate::stack) fn active_count(&self) -> usize {
        self.leases.len()
    }

    pub(in crate::stack) fn lease_newest_layers(&self) -> Vec<LayerRef> {
        self.leases
            .values()
            .filter_map(|lease| lease.manifest.layers.first().cloned())
            .collect()
    }
}

fn new_lease_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    format!("{nanos:032x}{:016x}", next_unique())
}

fn shared_registries() -> &'static Mutex<HashMap<String, Arc<Mutex<LeaseRegistry>>>> {
    static REGISTRIES: OnceLock<Mutex<HashMap<String, Arc<Mutex<LeaseRegistry>>>>> =
        OnceLock::new();
    REGISTRIES.get_or_init(|| Mutex::new(HashMap::new()))
}

pub(crate) fn reset_shared_registries_for_tests() {
    shared_registries()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .clear();
}
