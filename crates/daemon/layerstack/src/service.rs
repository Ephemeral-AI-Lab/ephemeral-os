use std::collections::{HashMap, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
use std::time::Instant;

use crate::model::{LayerChange, LayerPath, LayerRef, Manifest, MANIFEST_SCHEMA_VERSION};
use serde_json::{json, Value};

use crate::commit::{
    base_hashes_for_snapshot, ChangesetResult, CommitError, CommitOptions, CommitWriter,
};
use crate::{LayerStack, LayerStackError};

type RootService = Arc<CommitWriter>;

pub(crate) const SERVICE_CACHE_MAX: usize = 256;

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

    fn get(&mut self, key: &str, lock_wait_s: f64) -> Option<RootService> {
        self.record_lock_wait(lock_wait_s);
        let service = self.entries.get(key)?.clone();
        self.touch(key);
        self.stats.hits_total += 1;
        Some(service)
    }

    pub(crate) fn insert_or_get(
        &mut self,
        key: String,
        service: RootService,
        lock_wait_s: f64,
    ) -> RootService {
        self.record_lock_wait(lock_wait_s);
        if let Some(existing) = self.entries.get(&key).cloned() {
            self.touch(&key);
            self.stats.hits_total += 1;
            return existing;
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
        service
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

pub(crate) fn reset_service_cache_for_tests() {
    let mut cache = services()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    *cache = ServiceCache::default();
}

#[cfg(test)]
pub(crate) fn service_cache_contains_root_for_tests(root: &Path) -> bool {
    let key = normalize_root_key(root);
    let key_prefix = format!("{key}|");
    services()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .entries
        .keys()
        .any(|entry| entry == &key || entry.starts_with(&key_prefix))
}

fn service_for_root(root: &Path, options: CommitOptions) -> Result<RootService, CommitError> {
    let options = CommitOptions::new(options.auto_squash_max_depth);
    let key = service_cache_key(root, options);
    let lock_start = Instant::now();
    {
        let mut cache = lock_services()?;
        if let Some(service) = cache.get(&key, lock_start.elapsed().as_secs_f64()) {
            return Ok(service);
        }
    }
    let service = Arc::new(CommitWriter::with_options(root.to_path_buf(), options)?);
    let lock_start = Instant::now();
    let mut cache = lock_services()?;
    Ok(cache.insert_or_get(key, service, lock_start.elapsed().as_secs_f64()))
}

pub(crate) fn normalize_root_key(root: &Path) -> String {
    crate::fs::canonical_key(root)
}

fn service_cache_key(root: &Path, options: CommitOptions) -> String {
    format!(
        "{}|auto_squash_max_depth={}",
        normalize_root_key(root),
        options.auto_squash_max_depth
    )
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
    commit_direct_with_options(
        root,
        snapshot_version,
        changes,
        base_hashes,
        CommitOptions::default(),
    )
}

pub fn commit_direct_with_options(
    root: &Path,
    snapshot_version: Option<u64>,
    changes: &[LayerChange],
    base_hashes: &[(LayerPath, Option<String>)],
    options: CommitOptions,
) -> Result<ChangesetResult, CommitError> {
    service_for_root(root, options)?.apply_changeset_with_base_hashes(
        changes,
        snapshot_version,
        true,
        base_hashes,
    )
}

pub fn publish_capture(
    root: &Path,
    snapshot_manifest_version: i64,
    snapshot_layer_paths: &[PathBuf],
    changes: &[LayerChange],
) -> Result<ChangesetResult, CommitError> {
    publish_capture_with_options(
        root,
        snapshot_manifest_version,
        snapshot_layer_paths,
        changes,
        CommitOptions::default(),
    )
}

pub fn publish_capture_with_options(
    root: &Path,
    snapshot_manifest_version: i64,
    snapshot_layer_paths: &[PathBuf],
    changes: &[LayerChange],
    options: CommitOptions,
) -> Result<ChangesetResult, CommitError> {
    let manifest = snapshot_manifest(root, snapshot_manifest_version, snapshot_layer_paths)?;
    let base_hashes = base_hashes_for_snapshot(root, &manifest, changes)?;
    commit_direct_with_options(
        root,
        Some(manifest_version_u64(snapshot_manifest_version)?),
        changes,
        &base_hashes,
        options,
    )
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
    json!({
        "capacity": SERVICE_CACHE_MAX,
        "size": cache.entries.len(),
        "hits_total": cache.stats.hits_total,
        "misses_total": cache.stats.misses_total,
        "creates_total": cache.stats.creates_total,
        "evictions_total": cache.stats.evictions_total,
        "lock_wait_s_total": cache.stats.lock_wait_s_total,
        "lock_wait_s_max": cache.stats.lock_wait_s_max,
        "last_lock_wait_s": lock_wait_s,
    })
}

#[cfg(test)]
#[path = "../tests/unit/service.rs"]
mod tests;
