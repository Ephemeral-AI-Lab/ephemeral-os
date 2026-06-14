use std::collections::BTreeMap;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};
use std::time::Instant;

use serde::{Deserialize, Serialize};

use crate::model::{
    aggregate_layer_changes, layer_digest, manifest_root_hash, LayerChange, LayerRef, Manifest,
};

use crate::error::LayerStackError;
use crate::fs::{
    allocate_layer_dirs, clear_storage_root_preserving_lock_and_names, copy_path, fsync_dir,
    fsync_tree_files, join_layer_path, layer_digest_path, next_unique, read_manifest,
    record_elapsed, remove_path, replace_workspace_contents, resolve_layer_path,
    validate_layer_ref, write_atomic, write_layer_digest, write_manifest,
};
use crate::lock::{StorageWriterLockLease, STORAGE_WRITER_LOCK_FILE};
use crate::squash::{
    manifest_prefix_before_plan, LayerCheckpointSquasher, SquashPlanDecision, SquashPlanEntry,
};
use crate::workspace::build_workspace_base_from_snapshot;
use crate::{ACTIVE_MANIFEST_FILE, LAYERS_DIR, STAGING_DIR};

mod leases;
mod projection;
mod view;
mod whiteout;

pub(crate) use leases::reset_shared_registries_for_tests;
use leases::{
    lock_shared_registry, lock_shared_registry_recover, shared_registry_for_root, LeaseRegistry,
    SharedLeaseRegistry,
};
pub use view::MergedView;
use whiteout::{write_kernel_whiteout, OPAQUE_MARKER};

const COMMIT_WORKSPACE_JOURNAL_FILE: &str = "commit_to_workspace.json";

#[derive(Debug, Clone, PartialEq)]
pub struct Lease {
    pub lease_id: String,
    pub manifest_version: i64,
    pub root_hash: String,
    pub manifest: Manifest,
    pub layer_paths: Vec<String>,
}

#[derive(Debug)]
pub struct SquashOutcome {
    pub manifest: Option<Manifest>,
    pub lease_release_error: Option<LayerStackError>,
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
        recover_commit_to_workspace(&storage_root)?;
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

    fn read_active_manifest_unlocked(&self) -> Result<Manifest, LayerStackError> {
        read_manifest(self.storage_root.join(ACTIVE_MANIFEST_FILE))
    }

    pub(crate) fn with_active_manifest<T>(
        &self,
        f: impl FnOnce(&Manifest) -> Result<T, LayerStackError>,
    ) -> Result<T, LayerStackError> {
        let _guard = self.writer_lock.shared()?;
        let manifest = self.read_active_manifest_unlocked()?;
        f(&manifest)
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
            .map(|path| path.to_string_lossy().into_owned())
            .collect();
        Ok(Lease {
            lease_id: lease.lease_id,
            manifest_version: manifest.version,
            root_hash: manifest_root_hash(&manifest),
            manifest,
            layer_paths,
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

    pub(crate) fn squash_plan_decision(
        &self,
        max_depth: usize,
        min_reduction: usize,
    ) -> Result<(usize, SquashPlanDecision), LayerStackError> {
        let active = self.read_active_manifest()?;
        let depth = active.depth();
        let squasher = LayerCheckpointSquasher::new(self.storage_root.clone());
        let lease_head_layers = lock_shared_registry(&self.leases)?.lease_head_layers();
        let decision =
            squasher.plan_decision(&active, max_depth, &lease_head_layers, min_reduction)?;
        Ok((depth, decision))
    }

    pub fn squash(&mut self, max_depth: usize) -> Result<SquashOutcome, LayerStackError> {
        let _guard = self.writer_lock.exclusive()?;
        let active = self.read_active_manifest_unlocked()?;
        let squasher = LayerCheckpointSquasher::new(self.storage_root.clone());
        let lease_head_layers = {
            let leases = lock_shared_registry(&self.leases)?;
            leases.lease_head_layers()
        };
        let Some(plan) = squasher.plan(&active, max_depth, &lease_head_layers, 1)? else {
            return Ok(SquashOutcome {
                manifest: None,
                lease_release_error: None,
            });
        };
        let squash_lease = {
            let mut leases = lock_shared_registry(&self.leases)?;
            leases.acquire(active, &format!("squash-{}", next_unique()))?
        };

        let mut checkpoints = Vec::new();
        let mut committed = false;
        let outcome = (|| {
            for segment in plan.checkpoint_segments() {
                checkpoints.push(squasher.build_checkpoint(segment, plan.active_version)?);
            }

            let current = self.read_active_manifest_unlocked()?;
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
            (Ok(manifest), Ok(_)) => Ok(SquashOutcome {
                manifest,
                lease_release_error: None,
            }),
            (Ok(manifest), Err(release_err)) => {
                if committed {
                    Ok(SquashOutcome {
                        manifest,
                        lease_release_error: Some(release_err),
                    })
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
    pub fn active_lease_count(&self) -> usize {
        lock_shared_registry_recover(&self.leases).active_count()
    }

    pub fn storage_metrics(&self) -> Result<LayerStackStorageMetrics, LayerStackError> {
        let _guard = self.writer_lock.shared()?;
        let root = &self.storage_root;
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
        let _guard = self.writer_lock.exclusive()?;
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

        let active = self.read_active_manifest_unlocked()?;
        let projection = self.commit_projection_dir()?;
        let staged_storage = self.commit_staged_storage_dir()?;
        let mut timings = BTreeMap::new();
        let storage_root = self.storage_root.clone();
        let view = &mut self.view;
        let mut journal_requires_recovery = false;
        let outcome = (|| {
            let workspace_root_for_journal = workspace_root
                .canonicalize()
                .unwrap_or_else(|_| workspace_root.to_path_buf());
            let project_start = Instant::now();
            view.project(&projection, &active)?;
            record_elapsed(
                &mut timings,
                "layer_stack.commit_to_workspace.project_s",
                project_start,
            );

            let rebuild_start = Instant::now();
            let _ = build_workspace_base_from_snapshot(
                &staged_storage,
                &storage_root,
                workspace_root,
                &projection,
                false,
            )?;
            write_commit_workspace_journal(
                &storage_root,
                CommitWorkspacePhase::Staged,
                &staged_storage,
            )?;

            write_commit_workspace_journal(
                &storage_root,
                CommitWorkspacePhase::ReplacingWorkspace {
                    workspace_root: workspace_root_for_journal.to_string_lossy().into_owned(),
                },
                &staged_storage,
            )?;
            journal_requires_recovery = true;
            let replace_start = Instant::now();
            replace_workspace_contents(workspace_root, &projection)?;
            record_elapsed(
                &mut timings,
                "layer_stack.commit_to_workspace.replace_workspace_s",
                replace_start,
            );
            write_commit_workspace_journal(
                &storage_root,
                CommitWorkspacePhase::WorkspaceReplaced,
                &staged_storage,
            )?;
            journal_requires_recovery = true;

            install_staged_workspace_commit(&storage_root, &staged_storage)?;
            journal_requires_recovery = false;
            *view = MergedView::new(storage_root.clone());
            let new_manifest = read_manifest(storage_root.join(ACTIVE_MANIFEST_FILE))?;
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
        if outcome.is_err() && !journal_requires_recovery {
            let _ = remove_path(&staged_storage);
            let _ = remove_path(&commit_workspace_journal_path(&storage_root));
        }
        outcome.map(|manifest| (manifest, timings))
    }

    pub fn read_bytes(&self, path: &str) -> Result<(Option<Vec<u8>>, bool), LayerStackError> {
        self.read_bytes_limited(path, usize::MAX)
    }

    pub fn read_bytes_limited(
        &self,
        path: &str,
        max_bytes: usize,
    ) -> Result<(Option<Vec<u8>>, bool), LayerStackError> {
        let _guard = self.writer_lock.shared()?;
        let manifest = self.read_active_manifest_unlocked()?;
        self.view.read_bytes_limited(path, &manifest, max_bytes)
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
        let active = self.read_active_manifest_unlocked()?;
        if changes.is_empty() {
            return Ok(active);
        }

        let digest = layer_digest(changes);
        if self.head_layer_digest(&active)? == Some(digest.clone()) {
            return Ok(active);
        }

        let next_version = active.version + 1;
        let (layer_id, staging_dir, layer_dir) =
            allocate_layer_dirs(&self.storage_root, 'L', next_version)?;
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

        if let Err(err) = write_layer_digest(&self.storage_root, &layer_id, &digest) {
            let _ = remove_path(&layer_dir);
            return Err(err);
        }

        let latest = self.read_active_manifest_unlocked()?;
        if latest != active {
            let _ = remove_path(&layer_dir);
            let _ = std::fs::remove_file(layer_digest_path(&self.storage_root, &layer_id));
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
            let _ = std::fs::remove_file(layer_digest_path(&self.storage_root, &layer_id));
            return Err(err);
        }
        Ok(manifest)
    }

    fn head_layer_digest(&self, manifest: &Manifest) -> Result<Option<String>, LayerStackError> {
        let Some(head) = manifest.layers.first() else {
            return Ok(None);
        };
        let path = layer_digest_path(&self.storage_root, &head.layer_id);
        match std::fs::read_to_string(path) {
            Ok(value) => Ok(Some(value)),
            Err(err) if err.kind() == ErrorKind::NotFound => Ok(None),
            Err(err) => Err(err.into()),
        }
    }

    fn commit_projection_dir(&self) -> Result<PathBuf, LayerStackError> {
        allocate_commit_projection_dir(&self.storage_root, "projected")
    }

    fn commit_staged_storage_dir(&self) -> Result<PathBuf, LayerStackError> {
        let parent = self.storage_root.parent().ok_or_else(|| {
            LayerStackError::Storage(format!(
                "storage root has no parent: {}",
                self.storage_root.display()
            ))
        })?;
        std::fs::create_dir_all(parent)?;
        let prefix = staged_storage_name_prefix(&self.storage_root);
        for _ in 0..100 {
            let candidate =
                parent.join(format!("{prefix}{}-{}", std::process::id(), next_unique()));
            match std::fs::create_dir(&candidate) {
                Ok(()) => return Ok(candidate),
                Err(err) if err.kind() == ErrorKind::AlreadyExists => continue,
                Err(err) => return Err(err.into()),
            }
        }
        Err(LayerStackError::Storage(
            "could not allocate staged commit storage directory".to_owned(),
        ))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum CommitWorkspacePhase {
    Staged,
    ReplacingWorkspace { workspace_root: String },
    WorkspaceReplaced,
}

#[derive(Debug, Deserialize, Serialize)]
struct CommitWorkspaceJournal {
    phase: CommitWorkspacePhase,
    staged_storage_root: String,
}

fn commit_workspace_journal_path(storage_root: &Path) -> PathBuf {
    storage_root.join(COMMIT_WORKSPACE_JOURNAL_FILE)
}

fn write_commit_workspace_journal(
    storage_root: &Path,
    phase: CommitWorkspacePhase,
    staged_storage: &Path,
) -> Result<(), LayerStackError> {
    let journal = CommitWorkspaceJournal {
        phase,
        staged_storage_root: staged_storage.to_string_lossy().into_owned(),
    };
    let encoded = serde_json::to_vec_pretty(&journal)
        .map_err(|err| LayerStackError::Storage(err.to_string()))?;
    write_atomic(commit_workspace_journal_path(storage_root), &encoded)
}

fn recover_commit_to_workspace(storage_root: &Path) -> Result<(), LayerStackError> {
    let journal_path = commit_workspace_journal_path(storage_root);
    if !journal_path.exists() {
        return Ok(());
    }
    let journal = read_commit_workspace_journal(&journal_path)?;
    let staged_storage = validate_staged_storage_path(storage_root, &journal.staged_storage_root)?;
    match journal.phase {
        CommitWorkspacePhase::Staged => {
            remove_path(&staged_storage)?;
            remove_path(&journal_path)?;
            fsync_dir(storage_root)?;
            Ok(())
        }
        CommitWorkspacePhase::ReplacingWorkspace { workspace_root } => {
            let workspace_root = validate_workspace_root_path(&workspace_root)?;
            recover_workspace_replacement(storage_root, &staged_storage, &workspace_root)?;
            install_staged_workspace_commit(storage_root, &staged_storage)
        }
        CommitWorkspacePhase::WorkspaceReplaced => {
            install_staged_workspace_commit(storage_root, &staged_storage)
        }
    }
}

fn read_commit_workspace_journal(path: &Path) -> Result<CommitWorkspaceJournal, LayerStackError> {
    serde_json::from_str(&std::fs::read_to_string(path)?)
        .map_err(|err| LayerStackError::Storage(format!("read commit journal: {err}")))
}

fn recover_workspace_replacement(
    storage_root: &Path,
    staged_storage: &Path,
    workspace_root: &Path,
) -> Result<(), LayerStackError> {
    let active = read_manifest(staged_storage.join(ACTIVE_MANIFEST_FILE))?;
    let projection = allocate_commit_projection_dir(storage_root, "projected-recovery")?;
    let result = (|| {
        MergedView::new(staged_storage.to_path_buf()).project(&projection, &active)?;
        replace_workspace_contents(workspace_root, &projection)?;
        fsync_dir(workspace_root)?;
        Ok(())
    })();
    let _ = remove_path(&projection);
    result
}

fn allocate_commit_projection_dir(
    storage_root: &Path,
    prefix: &str,
) -> Result<PathBuf, LayerStackError> {
    let parent = storage_root.join("runtime").join("commit");
    std::fs::create_dir_all(&parent)?;
    for _ in 0..100 {
        let candidate = parent.join(format!("{prefix}-{}-{}", std::process::id(), next_unique()));
        match std::fs::create_dir(&candidate) {
            Ok(()) => return Ok(candidate),
            Err(err) if err.kind() == ErrorKind::AlreadyExists => continue,
            Err(err) => return Err(err.into()),
        }
    }
    Err(LayerStackError::Storage(format!(
        "could not allocate commit projection directory for prefix {prefix}"
    )))
}

fn validate_workspace_root_path(workspace_root: &str) -> Result<PathBuf, LayerStackError> {
    let path = PathBuf::from(workspace_root);
    if path.as_os_str().is_empty() {
        return Err(LayerStackError::Storage(
            "commit workspace path is empty".to_owned(),
        ));
    }
    if !path.is_absolute() {
        return Err(LayerStackError::Storage(format!(
            "commit workspace path must be absolute: {}",
            path.display()
        )));
    }
    Ok(path)
}

fn install_staged_workspace_commit(
    storage_root: &Path,
    staged_storage: &Path,
) -> Result<(), LayerStackError> {
    clear_storage_root_preserving_lock_and_names(storage_root, &[COMMIT_WORKSPACE_JOURNAL_FILE])?;
    for child in std::fs::read_dir(staged_storage)? {
        let child = child?;
        if child.file_name() == std::ffi::OsStr::new(STORAGE_WRITER_LOCK_FILE) {
            continue;
        }
        copy_path(&child.path(), &storage_root.join(child.file_name()))?;
    }
    fsync_tree_files(storage_root)?;
    fsync_dir(storage_root)?;
    remove_path(staged_storage)?;
    remove_path(&storage_root.join(COMMIT_WORKSPACE_JOURNAL_FILE))?;
    fsync_dir(storage_root)?;
    Ok(())
}

fn validate_staged_storage_path(
    storage_root: &Path,
    staged_storage_root: &str,
) -> Result<PathBuf, LayerStackError> {
    let path = PathBuf::from(staged_storage_root);
    let expected_parent = storage_root.parent().ok_or_else(|| {
        LayerStackError::Storage(format!(
            "storage root has no parent: {}",
            storage_root.display()
        ))
    })?;
    let file_name = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| {
            LayerStackError::Storage(format!(
                "staged commit storage path has no file name: {}",
                path.display()
            ))
        })?;
    if path.parent() != Some(expected_parent)
        || !file_name.starts_with(&staged_storage_name_prefix(storage_root))
    {
        return Err(LayerStackError::Storage(format!(
            "invalid staged commit storage path: {}",
            path.display()
        )));
    }
    Ok(path)
}

fn staged_storage_name_prefix(storage_root: &Path) -> String {
    let name = storage_root
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("layerstack");
    format!(".{name}.commit-storage-")
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
        match std::fs::remove_file(layer_digest_path(storage_root, &layer.layer_id)) {
            Ok(()) => {}
            Err(err) if err.kind() == ErrorKind::NotFound => {}
            Err(err) => return Err(err.into()),
        }
    }
    Ok(())
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

#[cfg(test)]
mod tests {
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::mpsc;
    use std::time::Duration;

    use super::*;
    use crate::workspace::{build_workspace_base, read_workspace_binding};

    type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

    #[test]
    fn commit_workspace_recovery_installs_workspace_replaced_journal() -> TestResult {
        let fixture = CommitFixture::new("recover-install")?;
        std::fs::write(fixture.workspace.join("tracked.txt"), "base\n")?;
        build_workspace_base(&fixture.root, &fixture.workspace, false)?;
        std::fs::create_dir_all(&fixture.snapshot)?;
        std::fs::write(fixture.snapshot.join("tracked.txt"), "committed\n")?;
        let stack = LayerStack::open(fixture.root.clone())?;
        let staged = stack.commit_staged_storage_dir()?;
        build_workspace_base_from_snapshot(
            &staged,
            &fixture.root,
            &fixture.workspace,
            &fixture.snapshot,
            false,
        )?;
        write_commit_workspace_journal(
            &fixture.root,
            CommitWorkspacePhase::WorkspaceReplaced,
            &staged,
        )?;
        clear_storage_root_preserving_lock_and_names(
            &fixture.root,
            &[COMMIT_WORKSPACE_JOURNAL_FILE],
        )?;
        drop(stack);

        let recovered = LayerStack::open(fixture.root.clone())?;

        assert_eq!(
            recovered.read_text("tracked.txt")?,
            ("committed\n".to_owned(), true)
        );
        let binding = read_workspace_binding(&fixture.root)?.expect("binding is recovered");
        assert_eq!(binding.workspace_root, fixture.workspace.to_string_lossy());
        assert_eq!(binding.layer_stack_root, fixture.root.to_string_lossy());
        assert!(!staged.exists(), "staged storage is removed after recovery");
        assert!(
            !fixture.root.join(COMMIT_WORKSPACE_JOURNAL_FILE).exists(),
            "commit journal is removed after recovery"
        );
        Ok(())
    }

    #[test]
    fn commit_workspace_recovery_discards_unreplaced_staged_journal() -> TestResult {
        let fixture = CommitFixture::new("recover-staged")?;
        std::fs::write(fixture.workspace.join("tracked.txt"), "base\n")?;
        build_workspace_base(&fixture.root, &fixture.workspace, false)?;
        std::fs::create_dir_all(&fixture.snapshot)?;
        std::fs::write(fixture.snapshot.join("tracked.txt"), "committed\n")?;
        let stack = LayerStack::open(fixture.root.clone())?;
        let staged = stack.commit_staged_storage_dir()?;
        build_workspace_base_from_snapshot(
            &staged,
            &fixture.root,
            &fixture.workspace,
            &fixture.snapshot,
            false,
        )?;
        write_commit_workspace_journal(&fixture.root, CommitWorkspacePhase::Staged, &staged)?;
        drop(stack);

        let recovered = LayerStack::open(fixture.root.clone())?;

        assert_eq!(
            recovered.read_text("tracked.txt")?,
            ("base\n".to_owned(), true)
        );
        assert!(
            !staged.exists(),
            "pre-replace staged storage is discarded during recovery"
        );
        assert!(
            !fixture.root.join(COMMIT_WORKSPACE_JOURNAL_FILE).exists(),
            "pre-replace journal is removed during recovery"
        );
        Ok(())
    }

    #[test]
    fn commit_workspace_recovery_retries_mid_replacement_journal() -> TestResult {
        let fixture = CommitFixture::new("recover-replacing")?;
        std::fs::write(fixture.workspace.join("tracked.txt"), "base\n")?;
        build_workspace_base(&fixture.root, &fixture.workspace, false)?;
        std::fs::create_dir_all(&fixture.snapshot)?;
        std::fs::write(fixture.snapshot.join("tracked.txt"), "committed\n")?;
        std::fs::write(fixture.snapshot.join("new.txt"), "new\n")?;
        let stack = LayerStack::open(fixture.root.clone())?;
        let staged = stack.commit_staged_storage_dir()?;
        build_workspace_base_from_snapshot(
            &staged,
            &fixture.root,
            &fixture.workspace,
            &fixture.snapshot,
            false,
        )?;
        write_commit_workspace_journal(
            &fixture.root,
            CommitWorkspacePhase::ReplacingWorkspace {
                workspace_root: fixture.workspace.to_string_lossy().into_owned(),
            },
            &staged,
        )?;
        std::fs::write(
            fixture.workspace.join("tracked.txt"),
            "partially replaced\n",
        )?;
        drop(stack);

        let recovered = LayerStack::open(fixture.root.clone())?;

        assert_eq!(
            std::fs::read_to_string(fixture.workspace.join("tracked.txt"))?,
            "committed\n"
        );
        assert_eq!(
            std::fs::read_to_string(fixture.workspace.join("new.txt"))?,
            "new\n"
        );
        assert_eq!(
            recovered.read_text("tracked.txt")?,
            ("committed\n".to_owned(), true)
        );
        assert!(!staged.exists(), "staged storage is removed after recovery");
        assert!(
            !fixture.root.join(COMMIT_WORKSPACE_JOURNAL_FILE).exists(),
            "commit journal is removed after recovery"
        );
        Ok(())
    }

    #[test]
    fn active_manifest_reads_wait_for_exclusive_storage_replacement() -> TestResult {
        let fixture = CommitFixture::new("read-blocks-replace")?;
        std::fs::write(fixture.workspace.join("tracked.txt"), "base\n")?;
        build_workspace_base(&fixture.root, &fixture.workspace, false)?;
        let stack = LayerStack::open(fixture.root.clone())?;
        let exclusive = stack.writer_lock.exclusive()?;
        remove_path(&fixture.root.join(ACTIVE_MANIFEST_FILE))?;

        let (version_tx, version_rx) = mpsc::channel();
        let root = fixture.root.clone();
        let reader = std::thread::spawn(move || -> TestResult {
            let version = LayerStack::open(root)?.read_active_manifest()?.version;
            version_tx.send(version)?;
            Ok(())
        });

        assert!(
            version_rx.recv_timeout(Duration::from_millis(50)).is_err(),
            "active manifest read observed transient storage state while exclusive replacement was held"
        );
        let manifest = Manifest::new(7, Vec::new(), crate::model::MANIFEST_SCHEMA_VERSION)?;
        write_manifest(fixture.root.join(ACTIVE_MANIFEST_FILE), &manifest)?;
        drop(exclusive);

        assert_eq!(version_rx.recv_timeout(Duration::from_secs(1))?, 7);
        reader
            .join()
            .map_err(|_| std::io::Error::other("reader thread panicked"))??;
        Ok(())
    }

    struct CommitFixture {
        root: PathBuf,
        workspace: PathBuf,
        snapshot: PathBuf,
    }

    impl CommitFixture {
        fn new(label: &str) -> TestResult<Self> {
            let base = std::env::temp_dir().join(format!(
                "layerstack-commit-{label}-{}-{}",
                std::process::id(),
                NEXT_COMMIT_TEST.fetch_add(1, Ordering::Relaxed)
            ));
            let root = base.join("layer-stack");
            let workspace = base.join("workspace");
            let snapshot = base.join("snapshot");
            let _ = std::fs::remove_dir_all(&base);
            std::fs::create_dir_all(&workspace)?;
            Ok(Self {
                root,
                workspace,
                snapshot,
            })
        }
    }

    impl Drop for CommitFixture {
        fn drop(&mut self) {
            if let Some(base) = self.root.parent() {
                let _ = std::fs::remove_dir_all(base);
            }
        }
    }

    static NEXT_COMMIT_TEST: AtomicU64 = AtomicU64::new(0);
}
