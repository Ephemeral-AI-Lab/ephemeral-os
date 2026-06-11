use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::ffi::OsString;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use crate::model::{
    aggregate_layer_changes, layer_digest, manifest_root_hash, LayerChange, LayerPath, LayerRef,
    Manifest,
};

use crate::error::LayerStackError;
use crate::fs::{
    clear_storage_root_preserving_lock, fsync_dir, fsync_tree_files, join_layer_path,
    read_manifest, record_elapsed, remove_path, replace_workspace_contents, resolve_layer_path,
    validate_layer_ref, write_atomic, write_manifest,
};
use crate::lock::StorageWriterLockLease;
use crate::squash::{manifest_prefix_before_plan, LayerCheckpointSquasher, SquashPlanEntry};
use crate::workspace::build_workspace_base;
use crate::{ACTIVE_MANIFEST_FILE, LAYERS_DIR, LAYER_METADATA_DIR, STAGING_DIR};

mod projection;
mod whiteout;

use whiteout::{is_kernel_whiteout, write_kernel_whiteout, LOGICAL_WHITEOUT_PREFIX, OPAQUE_MARKER};

#[derive(Debug, Clone, PartialEq)]
pub struct Lease {
    pub lease_id: String,
    pub manifest_version: i64,
    pub root_hash: String,
    pub manifest: Manifest,
    pub layer_paths: Vec<String>,
    pub timings: BTreeMap<String, f64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct LayerStackLeaseRecord {
    lease_id: String,
    manifest: Manifest,
}

#[derive(Debug, Default)]
struct LeaseRegistry {
    leases: HashMap<String, LayerStackLeaseRecord>,
    refcounts: BTreeMap<LayerRefKey, usize>,
}

type SharedLeaseRegistry = Arc<Mutex<LeaseRegistry>>;

fn shared_registry_for_root(storage_root: &Path) -> Result<SharedLeaseRegistry, LayerStackError> {
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
        .or_insert_with(|| Arc::new(Mutex::new(LeaseRegistry::default())))
        .clone())
}

fn lock_shared_registry(
    registry: &SharedLeaseRegistry,
) -> Result<MutexGuard<'_, LeaseRegistry>, LayerStackError> {
    registry
        .lock()
        .map_err(|_| LayerStackError::LockPoisoned("lease registry"))
}

fn lock_shared_registry_recover(registry: &SharedLeaseRegistry) -> MutexGuard<'_, LeaseRegistry> {
    registry
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

impl LeaseRegistry {
    fn acquire(
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

    fn release(&mut self, lease_id: &str) -> Option<LayerStackLeaseRecord> {
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

    fn leased_layers(&self) -> Vec<LayerRef> {
        self.refcounts.keys().map(LayerRef::from).collect()
    }

    fn lease_head_layers(&self) -> Vec<LayerRef> {
        self.leases
            .values()
            .filter_map(|lease| lease.manifest.layers.first())
            .map(LayerRefKey::from)
            .collect::<BTreeSet<_>>()
            .iter()
            .map(LayerRef::from)
            .collect()
    }

    fn active_count(&self) -> usize {
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
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    let counter = NEXT_LEASE.fetch_add(1, Ordering::Relaxed);
    format!("{nanos:032x}{counter:016x}")
}

fn shared_registries() -> &'static Mutex<HashMap<String, SharedLeaseRegistry>> {
    static REGISTRIES: OnceLock<Mutex<HashMap<String, SharedLeaseRegistry>>> = OnceLock::new();
    REGISTRIES.get_or_init(|| Mutex::new(HashMap::new()))
}

#[derive(Debug)]
pub struct MergedView {
    storage_root: PathBuf,
}

impl MergedView {
    #[must_use]
    pub const fn new(storage_root: PathBuf) -> Self {
        Self { storage_root }
    }

    pub fn read_bytes(
        &self,
        path: &str,
        manifest: &Manifest,
    ) -> Result<(Option<Vec<u8>>, bool), LayerStackError> {
        let rel = LayerPath::parse(path)?;
        for layer in &manifest.layers {
            let layer_dir = self.layer_dir(layer)?;
            if Self::is_whiteouted(&layer_dir, rel.as_str()) {
                return Ok((None, false));
            }
            if Self::lookup_blocked_by_layer(&layer_dir, rel.as_str()) {
                return Ok((None, false));
            }
            let target = join_layer_path(&layer_dir, rel.as_str());
            match std::fs::symlink_metadata(&target) {
                Ok(meta) if meta.file_type().is_symlink() => {
                    let target = std::fs::read_link(&target)
                        .map_err(|err| stale_layer_error(layer, rel.as_str(), &err))?;
                    return Ok((Some(target.to_string_lossy().as_bytes().to_vec()), true));
                }
                Ok(meta) if meta.is_file() => {
                    let bytes = std::fs::read(&target)
                        .map_err(|err| stale_layer_error(layer, rel.as_str(), &err))?;
                    return Ok((Some(bytes), true));
                }
                Ok(_) => return Err(stale_layer_error_value(layer, rel.as_str())),
                Err(err) if err.kind() == ErrorKind::NotFound => {}
                Err(err) => return Err(stale_layer_error(layer, rel.as_str(), &err)),
            }
        }
        Ok((None, false))
    }

    pub fn project(&self, destination: &Path, manifest: &Manifest) -> Result<(), LayerStackError> {
        remove_path(destination)?;
        std::fs::create_dir_all(destination)?;
        for layer in manifest.layers.iter().rev() {
            projection::apply_layer(&self.layer_dir(layer)?, destination)?;
        }
        Ok(())
    }

    fn layer_dir(&self, layer: &LayerRef) -> Result<PathBuf, LayerStackError> {
        validate_layer_ref(layer)?;
        let path = resolve_layer_path(&self.storage_root, &layer.path);
        if !path.is_dir() {
            return Err(LayerStackError::Storage(format!(
                "manifest references missing layer {}: {}",
                layer.layer_id, layer.path
            )));
        }
        Ok(path)
    }

    fn is_whiteouted(layer_dir: &Path, rel: &str) -> bool {
        if is_kernel_whiteout(&join_layer_path(layer_dir, rel)) {
            return true;
        }
        let rel_path = PathBuf::from(rel);
        let Some(name) = rel_path.file_name() else {
            return false;
        };
        let marker_name = {
            let mut marker = OsString::from(LOGICAL_WHITEOUT_PREFIX);
            marker.push(name);
            marker
        };
        let parent = rel_path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty());
        let marker = match parent {
            Some(parent) => layer_dir.join(parent).join(marker_name),
            None => layer_dir.join(marker_name),
        };
        marker.exists()
    }

    fn lookup_blocked_by_layer(layer_dir: &Path, rel: &str) -> bool {
        let parts: Vec<&str> = rel.split('/').collect();
        for index in 1..parts.len() {
            let ancestor = parts[..index].join("/");
            let path = join_layer_path(layer_dir, &ancestor);
            if is_kernel_whiteout(&path) {
                return true;
            }
            if let Ok(meta) = std::fs::symlink_metadata(&path) {
                if meta.is_file() || meta.file_type().is_symlink() {
                    return true;
                }
            }
            if path.join(OPAQUE_MARKER).exists() {
                return true;
            }
        }
        false
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LayerStackStorageMetrics {
    pub layer_dirs: usize,
    pub staging_dirs: usize,
    pub storage_bytes: u64,
}

#[derive(Debug)]
pub struct LayerStack {
    storage_root: PathBuf,
    writer_lock: StorageWriterLockLease,
    leases: SharedLeaseRegistry,
    view: MergedView,
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

    #[must_use]
    pub fn storage_root(&self) -> &Path {
        &self.storage_root
    }

    pub fn read_active_manifest(&self) -> Result<Manifest, LayerStackError> {
        read_manifest(self.storage_root.join(ACTIVE_MANIFEST_FILE))
    }

    pub fn acquire_snapshot(&self, owner_request_id: &str) -> Result<Lease, LayerStackError> {
        let _guard = self.writer_lock.shared()?;
        let manifest = self.read_active_manifest()?;
        let lease = {
            let mut leases = lock_shared_registry(&self.leases)?;
            leases.acquire(manifest.clone(), owner_request_id)?
        };
        let layer_paths = manifest
            .layers
            .iter()
            .map(|layer| resolve_layer_path(&self.storage_root, &layer.path))
            .map(|path| path.to_string_lossy().into_owned())
            .collect();
        Ok(Lease {
            lease_id: lease.lease_id,
            manifest_version: manifest.version,
            root_hash: manifest_root_hash(&manifest),
            manifest,
            layer_paths,
            timings: BTreeMap::new(),
        })
    }

    pub fn release_lease(&mut self, lease_id: &str) -> Result<bool, LayerStackError> {
        let _guard = self.writer_lock.exclusive()?;
        let mut leases = lock_shared_registry(&self.leases)?;
        release_lease_locked(&self.storage_root, &mut leases, lease_id)
    }

    pub fn can_squash(&self, max_depth: usize) -> Result<bool, LayerStackError> {
        let active = self.read_active_manifest()?;
        let squasher = LayerCheckpointSquasher::new(self.storage_root.clone());
        let lease_head_layers = lock_shared_registry(&self.leases)?.lease_head_layers();
        Ok(squasher
            .plan(&active, max_depth, &lease_head_layers, 2)?
            .is_some())
    }

    pub fn squash(&mut self, max_depth: usize) -> Result<Option<Manifest>, LayerStackError> {
        let _guard = self.writer_lock.exclusive()?;
        let active = self.read_active_manifest()?;
        let squasher = LayerCheckpointSquasher::new(self.storage_root.clone());
        let lease_head_layers = {
            let leases = lock_shared_registry(&self.leases)?;
            leases.lease_head_layers()
        };
        let Some(plan) = squasher.plan(&active, max_depth, &lease_head_layers, 1)? else {
            return Ok(None);
        };
        let squash_lease = {
            let mut leases = lock_shared_registry(&self.leases)?;
            leases.acquire(
                active,
                &format!("squash-{}", NEXT_LAYER.fetch_add(1, Ordering::Relaxed)),
            )?
        };

        let mut checkpoints = Vec::new();
        let mut committed = false;
        let outcome = (|| {
            for segment in plan.checkpoint_segments() {
                checkpoints.push(squasher.build_checkpoint(segment, plan.active_version)?);
            }

            let current = self.read_active_manifest()?;
            let Some(live_prefix) = manifest_prefix_before_plan(&current, &plan) else {
                return Ok(None);
            };
            let next_version = current.version + 1;
            let mut checkpoint_index = 0;
            let mut new_layers = live_prefix.to_vec();
            for entry in &plan.entries {
                match entry {
                    SquashPlanEntry::Keep(layer) => new_layers.push(layer.clone()),
                    SquashPlanEntry::Segment(_) => {
                        let mut checkpoint = checkpoints[checkpoint_index].clone();
                        let expected_prefix = format!("B{next_version:06}-");
                        if !checkpoint.layer_id.starts_with(&expected_prefix) {
                            checkpoint = squasher.relabel_checkpoint(&checkpoint, next_version)?;
                            checkpoints[checkpoint_index] = checkpoint.clone();
                        }
                        new_layers.push(checkpoint);
                        checkpoint_index += 1;
                    }
                }
            }
            let manifest = Manifest::new(next_version, new_layers, current.schema_version)
                .map_err(LayerStackError::from)?;
            write_manifest(self.storage_root.join(ACTIVE_MANIFEST_FILE), &manifest)?;
            committed = true;
            Ok(Some(manifest))
        })();

        if !committed {
            for checkpoint in &checkpoints {
                let _ = squasher.discard_checkpoint(checkpoint);
            }
        }
        let release = {
            let mut leases = lock_shared_registry(&self.leases)?;
            release_lease_locked(&self.storage_root, &mut leases, &squash_lease.lease_id)
        };
        match (outcome, release) {
            (Err(err), _) => Err(err),
            (Ok(manifest), Ok(_)) => Ok(manifest),
            (Ok(manifest), Err(release_err)) => {
                if committed {
                    Ok(manifest)
                } else {
                    Err(release_err)
                }
            }
        }
    }

    #[must_use]
    pub fn leased_layers(&self) -> Vec<LayerRef> {
        lock_shared_registry_recover(&self.leases).leased_layers()
    }

    #[must_use]
    pub fn lease_head_layers(&self) -> Vec<LayerRef> {
        lock_shared_registry_recover(&self.leases).lease_head_layers()
    }

    #[must_use]
    pub fn active_lease_count(&self) -> usize {
        lock_shared_registry_recover(&self.leases).active_count()
    }

    pub fn storage_metrics(&self) -> Result<LayerStackStorageMetrics, LayerStackError> {
        let root = self.storage_root();
        Ok(LayerStackStorageMetrics {
            layer_dirs: count_dirs(&root.join(LAYERS_DIR))?,
            staging_dirs: count_dirs(&root.join(STAGING_DIR))?,
            storage_bytes: storage_bytes(root)?,
        })
    }

    pub fn commit_to_workspace(
        &mut self,
        workspace_root: &Path,
    ) -> Result<(Manifest, BTreeMap<String, f64>), LayerStackError> {
        let writer_lock = StorageWriterLockLease::acquire(&self.storage_root)?;
        let _guard = writer_lock.exclusive()?;
        let total_start = Instant::now();
        if !workspace_root.is_dir() {
            return Err(LayerStackError::WorkspaceBinding(format!(
                "workspace_root does not exist: {}",
                workspace_root.display()
            )));
        }
        if lock_shared_registry(&self.leases)?.active_count() > 0 {
            return Err(LayerStackError::Storage(
                "commit_to_workspace blocked by active leases".to_owned(),
            ));
        }

        let active = self.read_active_manifest()?;
        let projection = self.commit_projection_dir()?;
        let mut timings = BTreeMap::new();
        let outcome = (|| {
            let project_start = Instant::now();
            self.view.project(&projection, &active)?;
            record_elapsed(
                &mut timings,
                "layer_stack.commit_to_workspace.project_s",
                project_start,
            );

            let replace_start = Instant::now();
            replace_workspace_contents(workspace_root, &projection)?;
            record_elapsed(
                &mut timings,
                "layer_stack.commit_to_workspace.replace_workspace_s",
                replace_start,
            );

            let rebuild_start = Instant::now();
            clear_storage_root_preserving_lock(&self.storage_root)?;
            let _ = build_workspace_base(&self.storage_root, workspace_root, false)?;
            self.view = MergedView::new(self.storage_root.clone());
            let new_manifest = self.read_active_manifest()?;
            record_elapsed(
                &mut timings,
                "layer_stack.commit_to_workspace.rebuild_base_s",
                rebuild_start,
            );
            record_elapsed(
                &mut timings,
                "layer_stack.commit_to_workspace.total_s",
                total_start,
            );
            Ok(new_manifest)
        })();
        let _ = remove_path(&projection);
        outcome.map(|manifest| (manifest, timings))
    }

    pub fn read_bytes(&self, path: &str) -> Result<(Option<Vec<u8>>, bool), LayerStackError> {
        self.view.read_bytes(path, &self.read_active_manifest()?)
    }

    pub fn read_text(&self, path: &str) -> Result<(String, bool), LayerStackError> {
        let (bytes, exists) = self.read_bytes(path)?;
        if !exists {
            return Ok((String::new(), false));
        }
        let bytes = bytes.unwrap_or_default();
        let text =
            String::from_utf8(bytes).map_err(|err| LayerStackError::Storage(err.to_string()))?;
        Ok((text, true))
    }

    pub fn publish_layer(&mut self, changes: &[LayerChange]) -> Result<Manifest, LayerStackError> {
        let _guard = self.writer_lock.exclusive()?;
        let active = self.read_active_manifest()?;
        if changes.is_empty() {
            return Ok(active);
        }

        let digest = layer_digest(changes);
        if self.head_layer_digest(&active)? == Some(digest.clone()) {
            return Ok(active);
        }

        let next_version = active.version + 1;
        let (layer_id, staging_dir, layer_dir) = self.allocate_layer_paths(next_version)?;
        std::fs::create_dir_all(&staging_dir)?;
        if let Err(err) = write_layer_changes(&staging_dir, changes)
            .and_then(|()| fsync_tree_files(&staging_dir))
            .and_then(|()| fsync_dir(&staging_dir))
        {
            let _ = std::fs::remove_dir_all(&staging_dir);
            return Err(err);
        }

        if let Err(err) = std::fs::rename(&staging_dir, &layer_dir) {
            let _ = std::fs::remove_dir_all(&staging_dir);
            return Err(err.into());
        }
        if let Some(parent) = layer_dir.parent() {
            fsync_dir(parent)?;
        }

        if let Err(err) = self.write_layer_digest(&layer_id, &digest) {
            let _ = remove_path(&layer_dir);
            return Err(err);
        }

        let latest = self.read_active_manifest()?;
        if latest != active {
            let _ = remove_path(&layer_dir);
            let _ = std::fs::remove_file(self.layer_digest_path(&layer_id));
            return Err(LayerStackError::ManifestConflict {
                expected: active.version,
                found: latest.version,
            });
        }

        let mut layers = Vec::with_capacity(active.layers.len() + 1);
        layers.push(LayerRef {
            layer_id: layer_id.clone(),
            path: format!("{LAYERS_DIR}/{layer_id}"),
        });
        layers.extend(active.layers);
        let manifest = Manifest::new(next_version, layers, active.schema_version)
            .map_err(LayerStackError::from)?;
        if let Err(err) = write_manifest(self.storage_root.join(ACTIVE_MANIFEST_FILE), &manifest) {
            let _ = remove_path(&layer_dir);
            let _ = std::fs::remove_file(self.layer_digest_path(&layer_id));
            return Err(err);
        }
        Ok(manifest)
    }

    fn allocate_layer_paths(
        &self,
        next_version: i64,
    ) -> Result<(String, PathBuf, PathBuf), LayerStackError> {
        for _ in 0..100 {
            let unique = NEXT_LAYER.fetch_add(1, Ordering::Relaxed);
            let layer_id = format!("L{next_version:06}-{unique:08x}");
            let staging_dir = self
                .storage_root
                .join(STAGING_DIR)
                .join(format!("{layer_id}.staging"));
            let layer_dir = self.storage_root.join(LAYERS_DIR).join(&layer_id);
            if !staging_dir.exists() && !layer_dir.exists() {
                return Ok((layer_id, staging_dir, layer_dir));
            }
        }
        Err(LayerStackError::LayerIdAllocation)
    }

    fn layer_digest_path(&self, layer_id: &str) -> PathBuf {
        layer_digest_path_at(&self.storage_root, layer_id)
    }

    fn head_layer_digest(&self, manifest: &Manifest) -> Result<Option<String>, LayerStackError> {
        let Some(head) = manifest.layers.first() else {
            return Ok(None);
        };
        let path = self.layer_digest_path(&head.layer_id);
        match std::fs::read_to_string(path) {
            Ok(value) => Ok(Some(value)),
            Err(err) if err.kind() == ErrorKind::NotFound => Ok(None),
            Err(err) => Err(err.into()),
        }
    }

    fn write_layer_digest(&self, layer_id: &str, digest: &str) -> Result<(), LayerStackError> {
        let path = self.layer_digest_path(layer_id);
        write_atomic(path, digest.as_bytes())
    }

    fn commit_projection_dir(&self) -> Result<PathBuf, LayerStackError> {
        let parent = self.storage_root.join("runtime").join("commit");
        std::fs::create_dir_all(&parent)?;
        for _ in 0..100 {
            let candidate = parent.join(format!(
                "projected-{}-{}",
                std::process::id(),
                NEXT_TMP_WRITE.fetch_add(1, Ordering::Relaxed)
            ));
            match std::fs::create_dir(&candidate) {
                Ok(()) => return Ok(candidate),
                Err(err) if err.kind() == ErrorKind::AlreadyExists => continue,
                Err(err) => return Err(err.into()),
            }
        }
        Err(LayerStackError::Storage(
            "could not allocate commit projection directory".to_owned(),
        ))
    }
}

fn release_lease_locked(
    storage_root: &Path,
    leases: &mut LeaseRegistry,
    lease_id: &str,
) -> Result<bool, LayerStackError> {
    let Some(lease) = leases.release(lease_id) else {
        return Ok(false);
    };
    let active = read_manifest(storage_root.join(ACTIVE_MANIFEST_FILE))?;
    let removable = unreferenced_layers(&lease.manifest.layers, &active, leases);
    remove_layers(storage_root, &removable)?;
    Ok(true)
}

fn unreferenced_layers(
    candidates: &[LayerRef],
    active: &Manifest,
    leases: &LeaseRegistry,
) -> Vec<LayerRef> {
    let retained_layers = leases.leased_layers();
    candidates
        .iter()
        .filter(|layer| !active.layers.contains(layer) && !retained_layers.contains(layer))
        .cloned()
        .collect()
}

fn remove_layers(storage_root: &Path, layers: &[LayerRef]) -> Result<(), LayerStackError> {
    for layer in layers {
        validate_layer_ref(layer)?;
        remove_path(&storage_root.join(&layer.path))?;
        match std::fs::remove_file(layer_digest_path_at(storage_root, &layer.layer_id)) {
            Ok(()) => {}
            Err(err) if err.kind() == ErrorKind::NotFound => {}
            Err(err) => return Err(err.into()),
        }
    }
    Ok(())
}

fn layer_digest_path_at(storage_root: &Path, layer_id: &str) -> PathBuf {
    storage_root
        .join(LAYER_METADATA_DIR)
        .join(format!("{layer_id}.digest"))
}

fn count_dirs(path: &Path) -> Result<usize, LayerStackError> {
    if !path.exists() {
        return Ok(0);
    }
    let mut count = 0;
    for entry in std::fs::read_dir(path)? {
        if entry?.file_type()?.is_dir() {
            count += 1;
        }
    }
    Ok(count)
}

fn storage_bytes(path: &Path) -> Result<u64, LayerStackError> {
    if !path.exists() {
        return Ok(0);
    }
    let mut total = 0;
    let mut stack = vec![path.to_path_buf()];
    while let Some(dir) = stack.pop() {
        for entry in std::fs::read_dir(dir)? {
            let entry = entry?;
            let meta = entry.metadata()?;
            if meta.is_dir() {
                stack.push(entry.path());
            } else if meta.is_file() {
                total += meta.len();
            }
        }
    }
    Ok(total)
}

fn write_layer_changes(layer_dir: &Path, changes: &[LayerChange]) -> Result<(), LayerStackError> {
    for change in aggregate_layer_changes(changes) {
        match change {
            LayerChange::Write { path, content } => {
                let target = join_layer_path(layer_dir, path.as_str());
                if let Some(parent) = target.parent() {
                    std::fs::create_dir_all(parent)?;
                }
                remove_path(&target)?;
                std::fs::write(target, content)?;
            }
            LayerChange::Delete { path } => {
                let target = join_layer_path(layer_dir, path.as_str());
                if let Some(parent) = target.parent() {
                    std::fs::create_dir_all(parent)?;
                }
                remove_path(&target)?;
                write_kernel_whiteout(&target)?;
            }
            LayerChange::Symlink { path, source_path } => {
                let target = join_layer_path(layer_dir, path.as_str());
                if let Some(parent) = target.parent() {
                    std::fs::create_dir_all(parent)?;
                }
                remove_path(&target)?;
                std::os::unix::fs::symlink(source_path, target)?;
            }
            LayerChange::OpaqueDir { path } => {
                let marker = join_layer_path(layer_dir, path.as_str()).join(OPAQUE_MARKER);
                if let Some(parent) = marker.parent() {
                    std::fs::create_dir_all(parent)?;
                }
                std::fs::write(marker, b"")?;
            }
        }
    }
    Ok(())
}

static NEXT_LAYER: AtomicU64 = AtomicU64::new(0);
static NEXT_TMP_WRITE: AtomicU64 = AtomicU64::new(0);
static NEXT_LEASE: AtomicU64 = AtomicU64::new(0);

fn stale_layer_error(layer: &LayerRef, rel: &str, err: &std::io::Error) -> LayerStackError {
    LayerStackError::Storage(format!(
        "layer no longer present while reading {rel}: {} ({err})",
        layer.layer_id
    ))
}

fn stale_layer_error_value(layer: &LayerRef, rel: &str) -> LayerStackError {
    LayerStackError::Storage(format!(
        "layer no longer present while reading {rel}: {}",
        layer.layer_id
    ))
}
