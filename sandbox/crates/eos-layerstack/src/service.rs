use std::collections::{BTreeMap, HashMap, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
use std::time::Instant;

use crate::model::{LayerChange, LayerPath, LayerRef, Manifest, MANIFEST_SCHEMA_VERSION};
use serde_json::{json, Value};

use crate::commit::{
    base_hashes_for_snapshot, usize_to_f64_saturating, ChangesetResult, CommitError, CommitQueue,
    CommitService, CommitTransaction,
};
use crate::commit::{insert_route_timings, route_metrics, StackRouteProvider};
use crate::{LayerStack, LayerStackError};

type RootService = Arc<CommitService>;

pub(crate) const SERVICE_CACHE_MAX: usize = 256;

pub(crate) struct ServiceLookup {
    pub(crate) service: RootService,
    pub(crate) lock_wait_s: f64,
    pub(crate) cache_hit: bool,
    pub(crate) cache_created: bool,
    pub(crate) evicted_count: usize,
    pub(crate) cache_size: usize,
}

impl ServiceLookup {
    fn insert_timings(&self, timings: &mut BTreeMap<String, f64>) {
        for (key, value) in [
            ("occ.runtime_service.cache_lock_wait_s", self.lock_wait_s),
            (
                "occ.runtime_service.cache_hit",
                if self.cache_hit { 1.0 } else { 0.0 },
            ),
            (
                "occ.runtime_service.cache_miss",
                if self.cache_hit { 0.0 } else { 1.0 },
            ),
            (
                "occ.runtime_service.cache_created",
                if self.cache_created { 1.0 } else { 0.0 },
            ),
            (
                "occ.runtime_service.cache_reused",
                if self.cache_created { 0.0 } else { 1.0 },
            ),
            (
                "occ.runtime_service.cache_evicted_count",
                usize_to_f64_saturating(self.evicted_count),
            ),
            (
                "occ.runtime_service.cache_size",
                usize_to_f64_saturating(self.cache_size),
            ),
            (
                "occ.runtime_service.cache_capacity",
                usize_to_f64_saturating(SERVICE_CACHE_MAX),
            ),
        ] {
            timings.entry(key.to_owned()).or_insert(value);
        }
    }
}

#[derive(Default)]
pub(crate) struct ServiceCacheStats {
    pub(crate) hits_total: u64,
    pub(crate) misses_total: u64,
    pub(crate) creates_total: u64,
    pub(crate) evictions_total: u64,
    pub(crate) lock_wait_s_total: f64,
    pub(crate) lock_wait_s_max: f64,
}

#[derive(Default)]
pub(crate) struct ServiceCache {
    pub(crate) entries: HashMap<String, RootService>,
    lru: VecDeque<String>,
    pub(crate) stats: ServiceCacheStats,
}

impl ServiceCache {
    fn record_lock_wait(&mut self, lock_wait_s: f64) {
        self.stats.lock_wait_s_total += lock_wait_s;
        self.stats.lock_wait_s_max = self.stats.lock_wait_s_max.max(lock_wait_s);
    }

    fn get(&mut self, key: &str, lock_wait_s: f64) -> Option<ServiceLookup> {
        self.record_lock_wait(lock_wait_s);
        let service = self.entries.get(key)?.clone();
        self.touch(key);
        self.stats.hits_total += 1;
        Some(ServiceLookup {
            service,
            lock_wait_s,
            cache_hit: true,
            cache_created: false,
            evicted_count: 0,
            cache_size: self.entries.len(),
        })
    }

    pub(crate) fn insert_or_get(
        &mut self,
        key: String,
        service: RootService,
        lock_wait_s: f64,
    ) -> (ServiceLookup, Option<RootService>) {
        self.record_lock_wait(lock_wait_s);
        if let Some(existing) = self.entries.get(&key).cloned() {
            self.touch(&key);
            self.stats.hits_total += 1;
            return (
                ServiceLookup {
                    service: existing,
                    lock_wait_s,
                    cache_hit: true,
                    cache_created: false,
                    evicted_count: 0,
                    cache_size: self.entries.len(),
                },
                Some(service),
            );
        }
        self.stats.misses_total += 1;
        self.stats.creates_total += 1;
        self.lru.push_back(key.clone());
        self.entries.insert(key, service.clone());
        let evicted_count = self.evict_oldest();
        self.stats.evictions_total = self
            .stats
            .evictions_total
            .saturating_add(u64::try_from(evicted_count).unwrap_or(u64::MAX));
        (
            ServiceLookup {
                service,
                lock_wait_s,
                cache_hit: false,
                cache_created: true,
                evicted_count,
                cache_size: self.entries.len(),
            },
            None,
        )
    }

    fn touch(&mut self, key: &str) {
        if let Some(position) = self.lru.iter().position(|entry| entry == key) {
            self.lru.remove(position);
        }
        self.lru.push_back(key.to_owned());
    }

    fn evict_oldest(&mut self) -> usize {
        let mut evicted_count = 0;
        while self.entries.len() > SERVICE_CACHE_MAX {
            let Some(key) = self.lru.pop_front() else {
                break;
            };
            if self.entries.remove(&key).is_some() {
                evicted_count += 1;
            }
        }
        evicted_count
    }
}

fn services() -> &'static Mutex<ServiceCache> {
    static SERVICES: OnceLock<Mutex<ServiceCache>> = OnceLock::new();
    SERVICES.get_or_init(|| Mutex::new(ServiceCache::default()))
}

fn lock_services() -> Result<MutexGuard<'static, ServiceCache>, CommitError> {
    services()
        .lock()
        .map_err(|_| CommitError::QueueStatePoisoned("per-root service registry"))
}

fn service_for_root(root: &Path) -> Result<ServiceLookup, CommitError> {
    let key = normalize_root_key(root);
    let lock_start = Instant::now();
    {
        let mut cache = lock_services()?;
        if let Some(lookup) = cache.get(&key, lock_start.elapsed().as_secs_f64()) {
            return Ok(lookup);
        }
    }
    let transaction = CommitTransaction {
        root: root.to_path_buf(),
    };
    let route_provider = Arc::new(StackRouteProvider {
        root: root.to_path_buf(),
    });
    let service = Arc::new(CommitService::with_route_provider(
        CommitQueue::new(transaction),
        route_provider,
    )?);
    let lock_start = Instant::now();
    let mut cache = lock_services()?;
    let (lookup, rejected) = cache.insert_or_get(key, service, lock_start.elapsed().as_secs_f64());
    drop(cache);
    drop(rejected);
    Ok(lookup)
}

pub(crate) fn normalize_root_key(root: &Path) -> String {
    root.canonicalize()
        .unwrap_or_else(|_| root.to_path_buf())
        .to_string_lossy()
        .into_owned()
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Snapshot {
    pub lease_id: String,
    pub manifest_version: i64,
    pub root_hash: String,
    pub layer_paths: Vec<PathBuf>,
}

pub fn acquire_snapshot(root: &Path, request_id: &str) -> Result<Snapshot, LayerStackError> {
    let lease = LayerStack::open(root.to_path_buf())?.acquire_snapshot(request_id)?;
    Ok(Snapshot {
        lease_id: lease.lease_id,
        manifest_version: lease.manifest_version,
        root_hash: lease.root_hash,
        layer_paths: lease.layer_paths.into_iter().map(PathBuf::from).collect(),
    })
}

pub fn release_lease(root: &Path, lease_id: &str) -> Result<bool, LayerStackError> {
    LayerStack::open(root.to_path_buf())?.release_lease(lease_id)
}

pub fn active_manifest(root: &Path) -> Result<Manifest, LayerStackError> {
    LayerStack::open(root.to_path_buf())?.read_active_manifest()
}

pub fn commit_direct(
    root: &Path,
    snapshot_version: Option<u64>,
    changes: &[LayerChange],
    base_hashes: &[(LayerPath, Option<String>)],
) -> Result<ChangesetResult, CommitError> {
    let lookup = service_for_root(root)?;
    let mut result = lookup.service.apply_changeset_with_base_hashes(
        changes,
        snapshot_version,
        true,
        base_hashes,
    )?;
    lookup.insert_timings(&mut result.timings);
    Ok(result)
}

pub fn publish_capture(
    root: &Path,
    snapshot_manifest_version: i64,
    snapshot_layer_paths: &[PathBuf],
    changes: &[LayerChange],
) -> Result<ChangesetResult, CommitError> {
    let route_start = Instant::now();
    let metrics = route_metrics(root, changes)?;
    let route_s = route_start.elapsed().as_secs_f64();
    let manifest = snapshot_manifest(root, snapshot_manifest_version, snapshot_layer_paths)?;
    let base_hashes = base_hashes_for_snapshot(root, &manifest, changes)?;
    let commit_start = Instant::now();
    let mut result = commit_direct(
        root,
        Some(manifest_version_u64(snapshot_manifest_version)?),
        changes,
        &base_hashes,
    )?;
    let commit_s = commit_start.elapsed().as_secs_f64();
    let mut timing_values = serde_json::Map::new();
    insert_route_timings(&mut timing_values, metrics, route_s, commit_s);
    for (key, value) in timing_values {
        if let Some(value) = value.as_f64() {
            result.timings.entry(key).or_insert(value);
        }
    }
    Ok(result)
}

fn snapshot_manifest(
    root: &Path,
    version: i64,
    layer_paths: &[PathBuf],
) -> Result<Manifest, CommitError> {
    let layers = layer_paths
        .iter()
        .enumerate()
        .map(|(index, path)| {
            let relative = match path.strip_prefix(root) {
                Ok(relative) => relative,
                Err(_) if path.is_relative() => path,
                Err(_) => {
                    return Err(CommitError::Storage(LayerStackError::Manifest(format!(
                        "snapshot layer path {} is outside {}",
                        path.display(),
                        root.display()
                    ))));
                }
            };
            Ok(LayerRef {
                layer_id: format!("snapshot-{index}"),
                path: relative.to_string_lossy().into_owned(),
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(Manifest::new(version, layers, MANIFEST_SCHEMA_VERSION)?)
}

pub fn manifest_version_u64(version: i64) -> Result<u64, LayerStackError> {
    u64::try_from(version).map_err(|_| {
        LayerStackError::Manifest(format!("manifest version must be non-negative: {version}"))
    })
}

#[must_use]
pub fn cache_snapshot() -> Value {
    let lock_start = Instant::now();
    let (
        size,
        hits_total,
        misses_total,
        creates_total,
        evictions_total,
        lock_wait_s_total,
        lock_wait_s_max,
        lock_wait_s,
    ) = {
        let mut cache = match lock_services() {
            Ok(cache) => cache,
            Err(err) => {
                return json!({
                    "capacity": SERVICE_CACHE_MAX,
                    "size": 0,
                    "poisoned": true,
                    "error": err.to_string(),
                });
            }
        };
        let lock_wait_s = lock_start.elapsed().as_secs_f64();
        cache.record_lock_wait(lock_wait_s);
        (
            cache.entries.len(),
            cache.stats.hits_total,
            cache.stats.misses_total,
            cache.stats.creates_total,
            cache.stats.evictions_total,
            cache.stats.lock_wait_s_total,
            cache.stats.lock_wait_s_max,
            lock_wait_s,
        )
    };
    json!({
        "capacity": SERVICE_CACHE_MAX,
        "size": size,
        "hits_total": hits_total,
        "misses_total": misses_total,
        "creates_total": creates_total,
        "evictions_total": evictions_total,
        "lock_wait_s_total": lock_wait_s_total,
        "lock_wait_s_max": lock_wait_s_max,
        "last_lock_wait_s": lock_wait_s,
    })
}

#[cfg(test)]
#[path = "../tests/unit/service.rs"]
mod tests;
