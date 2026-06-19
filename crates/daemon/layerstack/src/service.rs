use std::collections::{HashMap, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
use std::time::{Duration, Instant};

use crate::model::{LayerChange, LayerPath, LayerRef, Manifest, MANIFEST_SCHEMA_VERSION};

use crate::capture::{
    capture_upperdir_metadata, CaptureStats, CapturedUpperdirEntry, ProtectedPathDrop,
    MAX_CAPTURE_FILE_BYTES,
};
use crate::commit::{
    git_metadata::{is_canonical_loose_object_path, parts_after_git_dir},
    publish_command_decisions_for_manifest_with_protected_drops, CaptureRouteStats,
    ChangesetResult, CommitError, CommitOptions, CommitWriter, PublishDecision, Route,
    RouteDropReason,
};
use crate::{LayerStack, LayerStackError};

pub(crate) type RootService = Arc<CommitWriter>;

pub(crate) const SERVICE_CACHE_MAX: usize = 256;

#[derive(Default)]
pub(crate) struct ServiceCache {
    pub(crate) entries: HashMap<String, RootService>,
    lru: VecDeque<String>,
}

impl ServiceCache {
    fn get(&mut self, key: &str) -> Option<RootService> {
        let service = self.entries.get(key)?.clone();
        self.touch(key);
        Some(service)
    }

    pub(crate) fn insert_or_get(&mut self, key: String, service: RootService) -> RootService {
        if let Some(existing) = self.entries.get(&key).cloned() {
            self.touch(&key);
            return existing;
        }
        self.lru.push_back(key.clone());
        self.entries.insert(key, service.clone());
        self.evict_oldest();
        service
    }

    fn touch(&mut self, key: &str) {
        if let Some(position) = self.lru.iter().position(|entry| entry == key) {
            self.lru.remove(position);
        }
        self.lru.push_back(key.to_owned());
    }

    fn evict_oldest(&mut self) {
        while self.entries.len() > SERVICE_CACHE_MAX {
            let Some(key) = self.lru.pop_front() else {
                break;
            };
            self.entries.remove(&key);
        }
    }
}

pub(crate) fn services() -> &'static Mutex<ServiceCache> {
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

pub const IGNORED_LANE_FILE_LIMIT_DROP_REASON: &str = "ignored_lane_file_limit";
pub const IGNORED_LANE_BYTE_LIMIT_DROP_REASON: &str = "ignored_lane_byte_limit";
pub const IGNORED_FILE_BYTE_LIMIT_DROP_REASON: &str = "ignored_file_byte_limit";
pub const IGNORED_CAPTURE_DURATION_LIMIT_DROP_REASON: &str = "ignored_capture_duration_limit";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct IgnoredCaptureLimits {
    pub max_ignored_files: usize,
    pub max_ignored_bytes: u64,
    pub max_ignored_file_bytes: u64,
    pub spool_threshold_bytes: u64,
    pub max_metadata_capture_duration: Duration,
}

impl Default for IgnoredCaptureLimits {
    fn default() -> Self {
        Self {
            max_ignored_files: 4096,
            max_ignored_bytes: 64 * 1024 * 1024,
            max_ignored_file_bytes: 16 * 1024 * 1024,
            spool_threshold_bytes: 1024 * 1024,
            max_metadata_capture_duration: Duration::from_secs(30),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BoundedCaptureOptions {
    pub materialize_payloads: bool,
    pub ignored_limits: IgnoredCaptureLimits,
}

impl Default for BoundedCaptureOptions {
    fn default() -> Self {
        Self {
            materialize_payloads: true,
            ignored_limits: IgnoredCaptureLimits::default(),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct BoundedCapturedUpperdir {
    pub changes: Vec<LayerChange>,
    pub protected_drops: Vec<ProtectedPathDrop>,
    pub stats: CaptureStats,
    pub route_stats: CaptureRouteStats,
    pub metadata_path_count: usize,
    pub spool_dir: Option<PathBuf>,
}

fn service_for_root(root: &Path, options: CommitOptions) -> Result<RootService, CommitError> {
    let options = CommitOptions::new(options.auto_squash_max_depth);
    let key = service_cache_key(root, options);
    {
        let mut cache = lock_services()?;
        if let Some(service) = cache.get(&key) {
            return Ok(service);
        }
    }
    let service = Arc::new(CommitWriter::with_options(root.to_path_buf(), options)?);
    let mut cache = lock_services()?;
    Ok(cache.insert_or_get(key, service))
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SnapshotNormalization {
    pub triggered: bool,
    pub protected_layer_count: usize,
    pub checkpoint_count: usize,
    pub removed_layer_count: usize,
    pub bytes_added: u64,
    pub protected_pinned_bytes: u64,
    pub active_depth_before: usize,
    pub active_depth_after: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandSnapshot {
    pub snapshot: Snapshot,
    pub normalization: SnapshotNormalization,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SnapshotCompaction {
    pub manifest: Manifest,
    pub layer_paths: Vec<PathBuf>,
    pub before_layer_count: usize,
    pub after_layer_count: usize,
}

pub fn acquire_snapshot(root: &Path, request_id: &str) -> Result<Snapshot, LayerStackError> {
    let lease = LayerStack::open(root.to_path_buf())?.acquire_snapshot(request_id)?;
    Ok(snapshot_from_lease(lease))
}

pub(crate) fn acquire_bounded_snapshot_for_command(
    root: &Path,
    request_id: &str,
    max_depth: usize,
) -> Result<CommandSnapshot, LayerStackError> {
    let mut stack = LayerStack::open(root.to_path_buf())?;
    let bounded = stack.acquire_bounded_snapshot_for_command(request_id, max_depth)?;
    let normalization = SnapshotNormalization {
        triggered: bounded.copy_through.manifest.is_some(),
        protected_layer_count: bounded.copy_through.protected_layer_count,
        checkpoint_count: bounded.copy_through.checkpoint_count,
        removed_layer_count: bounded.copy_through.removed_layer_count,
        bytes_added: bounded.copy_through.bytes_added,
        protected_pinned_bytes: bounded.copy_through.protected_pinned_bytes,
        active_depth_before: bounded.copy_through.active_depth_before,
        active_depth_after: bounded.copy_through.active_depth_after,
    };
    Ok(CommandSnapshot {
        snapshot: snapshot_from_lease(bounded.lease),
        normalization,
    })
}

pub fn release_lease(root: &Path, lease_id: &str) -> Result<bool, LayerStackError> {
    LayerStack::open(root.to_path_buf())?.release_lease(lease_id)
}

pub(crate) fn compact_snapshot_for_remount(
    root: &Path,
    snapshot_manifest_version: i64,
    snapshot_layer_paths: &[PathBuf],
) -> Result<SnapshotCompaction, CommitError> {
    let manifest = snapshot_manifest_preserving_layer_ids(
        root,
        snapshot_manifest_version,
        snapshot_layer_paths,
    )?;
    let mut stack = LayerStack::open(root.to_path_buf())?;
    let layer = stack.build_compaction_checkpoint(&manifest)?;
    let compact_manifest = Manifest::new(
        manifest.version,
        vec![layer.clone()],
        manifest.schema_version,
    )?;
    let layer_path = crate::fs::resolve_layer_path(root, &layer.path);
    Ok(SnapshotCompaction {
        manifest: compact_manifest,
        layer_paths: vec![layer_path],
        before_layer_count: manifest.layers.len(),
        after_layer_count: 1,
    })
}

pub fn publish_command_capture_lane_aware(
    root: &Path,
    snapshot_manifest_version: i64,
    snapshot_layer_paths: &[PathBuf],
    changes: &[LayerChange],
    protected_drops: &[ProtectedPathDrop],
    options: CommitOptions,
) -> Result<ChangesetResult, CommitError> {
    let manifest = snapshot_manifest(root, snapshot_manifest_version, snapshot_layer_paths)?;
    let decisions = publish_command_decisions_for_manifest_with_protected_drops(
        root,
        &manifest,
        changes,
        protected_drops,
    )?;
    service_for_root(root, options)?.apply_command_lane_aware_changeset(
        changes,
        Some(manifest_version_u64(snapshot_manifest_version)?),
        decisions,
    )
}

pub fn capture_upperdir_for_snapshot_with_options(
    root: &Path,
    snapshot_manifest_version: i64,
    snapshot_layer_paths: &[PathBuf],
    upperdir: &Path,
    spool_dir: &Path,
    options: BoundedCaptureOptions,
) -> Result<BoundedCapturedUpperdir, CommitError> {
    let capture_start = Instant::now();
    let manifest = snapshot_manifest(root, snapshot_manifest_version, snapshot_layer_paths)?;
    let metadata = capture_upperdir_metadata(upperdir).map_err(CommitError::from)?;
    let metadata_elapsed = capture_start.elapsed();
    let placeholder_changes = metadata
        .entries
        .iter()
        .map(command_route_probe_change)
        .collect::<Result<Vec<_>, CommitError>>()?;
    let decisions = publish_command_decisions_for_manifest_with_protected_drops(
        root,
        &manifest,
        &placeholder_changes,
        &metadata.protected_drops,
    )?;
    let mut route_stats = capture_route_stats_from_metadata(
        &metadata.entries,
        &decisions,
        metadata.protected_drops.len(),
    );
    route_stats.ignored_limit_drop_reason = ignored_limit_drop_reason(
        &metadata.entries,
        &decisions,
        &options.ignored_limits,
        metadata_elapsed,
    );

    if options.materialize_payloads {
        let _ = std::fs::remove_dir_all(spool_dir);
    }
    let capture_result = materialize_bounded_capture_changes(
        &metadata.entries,
        &decisions,
        route_stats.ignored_limit_drop_reason.is_some(),
        spool_dir,
        &options,
        &mut route_stats,
    );
    if capture_result.is_err() && options.materialize_payloads {
        let _ = std::fs::remove_dir_all(spool_dir);
    }
    let changes = capture_result?;
    let spool_dir = (route_stats.direct_spooled_bytes > 0).then(|| spool_dir.to_path_buf());
    Ok(BoundedCapturedUpperdir {
        changes,
        protected_drops: metadata.protected_drops,
        stats: metadata.stats,
        route_stats,
        metadata_path_count: metadata.entries.len(),
        spool_dir,
    })
}

fn command_route_probe_change(entry: &CapturedUpperdirEntry) -> Result<LayerChange, CommitError> {
    if command_git_metadata_probe_needs_payload(entry.path()) && entry.regular_file_size().is_some()
    {
        entry
            .materialize_in_memory(MAX_CAPTURE_FILE_BYTES)
            .map_err(CommitError::from)
    } else {
        Ok(entry.placeholder_change())
    }
}

fn command_git_metadata_probe_needs_payload(path: &LayerPath) -> bool {
    let Some(parts) = parts_after_git_dir(path) else {
        return false;
    };
    parts == ["index"]
        || parts.first().is_some_and(|part| matches!(*part, "logs"))
        || is_canonical_loose_object_path(&parts)
}

fn capture_route_stats_from_metadata(
    entries: &[CapturedUpperdirEntry],
    decisions: &[PublishDecision],
    protected_drop_count: usize,
) -> CaptureRouteStats {
    let mut stats = CaptureRouteStats::default();
    for (index, decision) in decisions.iter().enumerate() {
        match decision.route {
            Route::Gated => stats.gated_path_count += 1,
            Route::Direct => {
                stats.direct_path_count += 1;
                if let Some(entry) = entries.get(index) {
                    stats.direct_bytes = stats
                        .direct_bytes
                        .saturating_add(entry.regular_file_size().unwrap_or(0));
                }
            }
            Route::Drop => {
                stats.drop_path_count += 1;
                if let Some(reason) = decision.drop_reason {
                    stats.record_drop_reason(reason.as_str());
                }
            }
        }
    }
    debug_assert!(decisions.len() >= entries.len().saturating_add(protected_drop_count));
    stats
}

fn ignored_limit_drop_reason(
    entries: &[CapturedUpperdirEntry],
    decisions: &[PublishDecision],
    limits: &IgnoredCaptureLimits,
    metadata_elapsed: Duration,
) -> Option<String> {
    let mut ignored_file_count = 0_usize;
    let mut ignored_bytes = 0_u64;
    for (entry, decision) in entries.iter().zip(decisions.iter()) {
        if decision.route != Route::Direct {
            continue;
        }
        let Some(size) = entry.regular_file_size() else {
            continue;
        };
        if size > limits.max_ignored_file_bytes {
            return Some(IGNORED_FILE_BYTE_LIMIT_DROP_REASON.to_owned());
        }
        ignored_file_count = ignored_file_count.saturating_add(1);
        ignored_bytes = ignored_bytes.saturating_add(size);
    }
    if ignored_file_count > limits.max_ignored_files {
        return Some(IGNORED_LANE_FILE_LIMIT_DROP_REASON.to_owned());
    }
    if ignored_bytes > limits.max_ignored_bytes {
        return Some(IGNORED_LANE_BYTE_LIMIT_DROP_REASON.to_owned());
    }
    if metadata_elapsed > limits.max_metadata_capture_duration {
        return Some(IGNORED_CAPTURE_DURATION_LIMIT_DROP_REASON.to_owned());
    }
    None
}

fn materialize_bounded_capture_changes(
    entries: &[CapturedUpperdirEntry],
    decisions: &[PublishDecision],
    ignored_lane_dropped: bool,
    spool_dir: &Path,
    options: &BoundedCaptureOptions,
    route_stats: &mut CaptureRouteStats,
) -> Result<Vec<LayerChange>, CommitError> {
    if !options.materialize_payloads {
        return Ok(Vec::new());
    }
    let spool_direct_writes = !ignored_lane_dropped
        && route_stats.direct_bytes > options.ignored_limits.spool_threshold_bytes;
    let mut changes = Vec::new();
    for (index, entry) in entries.iter().enumerate() {
        let Some(decision) = decisions.get(index) else {
            return Err(CommitError::RoutePreparation(format!(
                "missing route decision for captured path {}",
                entry.path().as_str()
            )));
        };
        match decision.route {
            Route::Gated => changes.push(entry.materialize_in_memory(MAX_CAPTURE_FILE_BYTES)?),
            Route::Direct if ignored_lane_dropped => {}
            Route::Direct => {
                changes.push(materialize_direct_entry(
                    entry,
                    index,
                    spool_dir,
                    &options.ignored_limits,
                    route_stats,
                    spool_direct_writes,
                )?);
            }
            Route::Drop if materialize_dropped_command_entry(entry, decision) => {
                changes.push(entry.materialize_in_memory(MAX_CAPTURE_FILE_BYTES)?);
            }
            Route::Drop => changes.push(entry.placeholder_change()),
        }
    }
    Ok(changes)
}

fn materialize_dropped_command_entry(
    entry: &CapturedUpperdirEntry,
    decision: &PublishDecision,
) -> bool {
    entry.regular_file_size().is_some()
        && decision.drop_reason == Some(RouteDropReason::GitIndexStatRefresh)
}

fn materialize_direct_entry(
    entry: &CapturedUpperdirEntry,
    index: usize,
    spool_dir: &Path,
    limits: &IgnoredCaptureLimits,
    route_stats: &mut CaptureRouteStats,
    spool_direct_writes: bool,
) -> Result<LayerChange, CommitError> {
    let max_file_bytes = usize::try_from(limits.max_ignored_file_bytes).unwrap_or(usize::MAX);
    match entry.regular_file_size() {
        Some(size) if spool_direct_writes && size > 0 => {
            let spool_path = spool_dir.join(format!("{index:016x}.payload"));
            let change = entry.materialize_spooled(&spool_path, max_file_bytes)?;
            route_stats.direct_spooled_bytes =
                route_stats.direct_spooled_bytes.saturating_add(size);
            Ok(change)
        }
        _ => entry
            .materialize_in_memory(max_file_bytes)
            .map_err(CommitError::from),
    }
}

pub(crate) fn snapshot_manifest(
    root: &Path,
    version: i64,
    layer_paths: &[PathBuf],
) -> Result<Manifest, CommitError> {
    snapshot_manifest_with_layer_ids(root, version, layer_paths, false)
}

pub(crate) fn snapshot_manifest_preserving_layer_ids(
    root: &Path,
    version: i64,
    layer_paths: &[PathBuf],
) -> Result<Manifest, CommitError> {
    snapshot_manifest_with_layer_ids(root, version, layer_paths, true)
}

fn snapshot_manifest_with_layer_ids(
    root: &Path,
    version: i64,
    layer_paths: &[PathBuf],
    preserve_layer_ids: bool,
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
            let relative_path = relative.to_string_lossy().into_owned();
            let layer_id = if preserve_layer_ids {
                layer_id_from_relative_path(relative).unwrap_or_else(|| format!("snapshot-{index}"))
            } else {
                format!("snapshot-{index}")
            };
            Ok(LayerRef {
                layer_id,
                path: relative_path,
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(Manifest::new(version, layers, MANIFEST_SCHEMA_VERSION)?)
}

fn layer_id_from_relative_path(relative: &Path) -> Option<String> {
    let mut components = relative.components();
    let first = components.next()?.as_os_str();
    if first != std::ffi::OsStr::new(crate::LAYERS_DIR) {
        return None;
    }
    let layer_id = components
        .next()?
        .as_os_str()
        .to_string_lossy()
        .into_owned();
    if components.next().is_some() || layer_id.is_empty() {
        return None;
    }
    Some(layer_id)
}

fn snapshot_from_lease(lease: crate::Lease) -> Snapshot {
    Snapshot {
        lease_id: lease.lease_id,
        manifest_version: lease.manifest_version,
        root_hash: lease.root_hash,
        layer_paths: lease.layer_paths.into_iter().map(PathBuf::from).collect(),
    }
}

fn manifest_version_u64(version: i64) -> Result<u64, LayerStackError> {
    u64::try_from(version).map_err(|_| {
        LayerStackError::Manifest(format!("manifest version must be non-negative: {version}"))
    })
}
