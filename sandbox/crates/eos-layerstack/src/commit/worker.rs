use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{mpsc, Mutex};
use std::time::{Duration, Instant};

use crate::model::{LayerChange, LayerPath, Manifest};
use crate::{LayerStack, MergedView, AUTO_SQUASH_MAX_DEPTH};

use super::{
    hash_current, i64_to_f64_saturating, usize_to_f64_saturating, ChangesetResult, CommitError,
    CommitStatus, FileResult, PublishDecision, Route,
};

pub(crate) const COMMIT_QUEUE_THREAD_NAME: &str = "occ-commit-queue";

pub(crate) const MAX_BATCH_SIZE: usize = 64;

pub(crate) const BATCH_WINDOW_S: f64 = 0.002;

pub(crate) const MAX_OCC_CAS_RETRIES: u32 = 3;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreparedChangeset {
    pub snapshot_version: Option<u64>,
    pub path_groups: Vec<PublishDecision>,
    pub changes: Vec<LayerChange>,
    pub atomic: bool,
}

pub trait CommitTransactionPort: Send {
    fn revalidate_and_publish(
        &self,
        combined: &PreparedChangeset,
    ) -> Result<ChangesetResult, PublishConflict>;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PublishConflict {
    pub observed_version: Option<u64>,
}

struct WorkItem {
    prepared: PreparedChangeset,
    reply: mpsc::Sender<Result<ChangesetResult, CommitError>>,
    enqueued_at: Instant,
}

enum QueueItem {
    Work(WorkItem),
    Stop,
}

pub struct CommitQueue<T: CommitTransactionPort + 'static> {
    sender: mpsc::Sender<QueueItem>,
    receiver: Mutex<Option<mpsc::Receiver<QueueItem>>>,
    transaction: Mutex<Option<T>>,
    handle: Option<std::thread::JoinHandle<()>>,
    max_batch_size: usize,
    batch_window_s: f64,
    max_cas_retries: u32,
    closed: bool,
}

struct CommitWorker<T: CommitTransactionPort + 'static> {
    receiver: mpsc::Receiver<QueueItem>,
    transaction: T,
    max_batch_size: usize,
    batch_window_s: f64,
    max_cas_retries: u32,
}

impl<T: CommitTransactionPort + 'static> CommitQueue<T> {
    pub fn new(transaction: T) -> Self {
        Self::with_config(
            transaction,
            MAX_BATCH_SIZE,
            BATCH_WINDOW_S,
            MAX_OCC_CAS_RETRIES,
        )
    }

    pub fn with_config(
        transaction: T,
        max_batch_size: usize,
        batch_window_s: f64,
        max_cas_retries: u32,
    ) -> Self {
        let (sender, receiver) = mpsc::channel();
        Self {
            sender,
            receiver: Mutex::new(Some(receiver)),
            transaction: Mutex::new(Some(transaction)),
            handle: None,
            max_batch_size: max_batch_size.max(1),
            batch_window_s: batch_window_s.max(0.0),
            max_cas_retries: max_cas_retries.max(1),
            closed: false,
        }
    }

    pub fn start(&mut self) -> Result<(), CommitError> {
        if self.closed {
            return Err(CommitError::QueueClosed);
        }
        if self
            .handle
            .as_ref()
            .is_some_and(|handle| !handle.is_finished())
        {
            return Ok(());
        }
        let receiver = self
            .receiver
            .lock()
            .map_err(|_| CommitError::QueueStatePoisoned("receiver slot"))?
            .take()
            .ok_or(CommitError::QueueNotStarted)?;
        let transaction = self
            .transaction
            .lock()
            .map_err(|_| CommitError::QueueStatePoisoned("transaction slot"))?
            .take()
            .ok_or(CommitError::QueueNotStarted)?;
        let worker = CommitWorker {
            receiver,
            transaction,
            max_batch_size: self.max_batch_size,
            batch_window_s: self.batch_window_s,
            max_cas_retries: self.max_cas_retries,
        };
        let handle = std::thread::Builder::new()
            .name(COMMIT_QUEUE_THREAD_NAME.to_owned())
            .spawn(move || {
                worker.run();
            })
            .map_err(|err| CommitError::WorkerStart(err.to_string()))?;
        self.handle = Some(handle);
        Ok(())
    }

    pub fn close(&mut self) -> Result<(), CommitError> {
        if self.closed {
            return Ok(());
        }
        self.closed = true;
        if self.handle.is_none() {
            return Ok(());
        }
        let _ = self.sender.send(QueueItem::Stop);
        self.handle.take().map_or(Ok(()), |handle| {
            handle.join().map_err(|_| CommitError::WorkerPanicked)
        })
    }

    pub fn submit(
        &self,
        prepared: PreparedChangeset,
    ) -> Result<mpsc::Receiver<Result<ChangesetResult, CommitError>>, CommitError> {
        if self.closed {
            return Err(CommitError::QueueClosed);
        }
        if self
            .handle
            .as_ref()
            .is_none_or(std::thread::JoinHandle::is_finished)
        {
            return Err(CommitError::QueueNotStarted);
        }
        let (reply, receiver) = mpsc::channel();
        self.sender
            .send(QueueItem::Work(WorkItem {
                prepared,
                reply,
                enqueued_at: Instant::now(),
            }))
            .map_err(|_| CommitError::QueueClosed)?;
        Ok(receiver)
    }

    fn commit_batch(transaction: &T, batch: Vec<WorkItem>, max_cas_retries: u32) {
        let commit_start = Instant::now();
        let Some(combined) = combine_prepared(batch.iter().map(|item| &item.prepared)) else {
            return;
        };
        let mut attempts = 0;
        let result = loop {
            match transaction.revalidate_and_publish(&combined) {
                Ok(result) => break result,
                Err(conflict) => {
                    attempts += 1;
                    if attempts >= max_cas_retries {
                        break cas_exhaustion_result(&combined, &conflict, max_cas_retries);
                    }
                }
            }
        };
        let commit_elapsed_s = commit_start.elapsed().as_secs_f64();
        let batch_size = usize_to_f64_saturating(batch.len());
        for item in batch {
            let files = result_files_for_item(&result, &item.prepared);
            let mut timings = result.timings.clone();
            timings.insert(
                "occ.serial.queue_wait_s".to_owned(),
                commit_start.duration_since(item.enqueued_at).as_secs_f64(),
            );
            timings.insert("occ.serial.batch_size".to_owned(), batch_size);
            timings.insert("occ.serial.commit_s".to_owned(), commit_elapsed_s);
            timings.insert(
                "occ.serial.cas_attempts".to_owned(),
                f64::from(attempts + 1),
            );
            let _ = item.reply.send(Ok(ChangesetResult {
                files,
                published_manifest_version: result.published_manifest_version,
                timings,
            }));
        }
    }
}

impl<T: CommitTransactionPort + 'static> CommitWorker<T> {
    fn run(self) {
        while let Ok(first) = self.receiver.recv() {
            let QueueItem::Work(first) = first else {
                return;
            };
            let mut items = vec![first];
            let mut stop_seen = drain_ready(&self.receiver, &mut items, self.max_batch_size);
            if !stop_seen && self.batch_window_s > 0.0 && items.len() < self.max_batch_size {
                std::thread::sleep(Duration::from_secs_f64(self.batch_window_s));
                stop_seen = drain_ready(&self.receiver, &mut items, self.max_batch_size);
            }
            for batch in disjoint_batches(items) {
                CommitQueue::<T>::commit_batch(&self.transaction, batch, self.max_cas_retries);
            }
            if stop_seen {
                return;
            }
        }
    }
}

fn drain_ready(
    receiver: &mpsc::Receiver<QueueItem>,
    items: &mut Vec<WorkItem>,
    max_batch_size: usize,
) -> bool {
    while items.len() < max_batch_size {
        match receiver.try_recv() {
            Ok(QueueItem::Work(item)) => items.push(item),
            Ok(QueueItem::Stop) | Err(mpsc::TryRecvError::Disconnected) => return true,
            Err(mpsc::TryRecvError::Empty) => return false,
        }
    }
    false
}

fn disjoint_batches(items: Vec<WorkItem>) -> Vec<Vec<WorkItem>> {
    let mut pending: Vec<(WorkItem, HashSet<String>)> = items
        .into_iter()
        .map(|item| {
            let paths = item
                .prepared
                .path_groups
                .iter()
                .map(|group| group.path.as_str().to_owned())
                .collect();
            (item, paths)
        })
        .collect();
    let mut batches = Vec::new();
    while !pending.is_empty() {
        let mut used = HashSet::new();
        let mut batch = Vec::new();
        let mut rest = Vec::new();
        for (item, paths) in pending {
            if item.prepared.atomic || !used.is_disjoint(&paths) {
                rest.push((item, paths));
            } else {
                used.extend(paths.iter().cloned());
                batch.push(item);
            }
        }
        if batch.is_empty() {
            let (item, _) = rest.remove(0);
            batch.push(item);
        }
        batches.push(batch);
        pending = rest;
    }
    batches
}

fn combine_prepared<'a>(
    items: impl Iterator<Item = &'a PreparedChangeset>,
) -> Option<PreparedChangeset> {
    let items: Vec<&PreparedChangeset> = items.collect();
    let first = items.first()?;
    debug_assert!(items.len() == 1 || !items.iter().any(|prepared| prepared.atomic));
    Some(PreparedChangeset {
        snapshot_version: first.snapshot_version,
        path_groups: items
            .iter()
            .flat_map(|prepared| prepared.path_groups.iter().cloned())
            .collect(),
        changes: items
            .iter()
            .flat_map(|prepared| prepared.changes.iter().cloned())
            .collect(),
        atomic: first.atomic,
    })
}

fn result_files_for_item(
    result: &ChangesetResult,
    prepared: &PreparedChangeset,
) -> Vec<FileResult> {
    prepared
        .path_groups
        .iter()
        .filter_map(|group| {
            result
                .files
                .iter()
                .find(|file| file.path == group.path)
                .cloned()
        })
        .collect()
}

fn cas_exhaustion_result(
    prepared: &PreparedChangeset,
    conflict: &PublishConflict,
    max_cas_retries: u32,
) -> ChangesetResult {
    let message = format!(
        "CAS mismatch retry budget exhausted after {max_cas_retries} attempts: observed version {:?}",
        conflict.observed_version
    );
    let files = prepared
        .path_groups
        .iter()
        .map(|group| {
            let (status, message) = match group.route {
                Route::Drop => (
                    CommitStatus::Dropped,
                    group.message.clone().unwrap_or_default(),
                ),
                Route::Reject => (
                    CommitStatus::Rejected,
                    group.message.clone().unwrap_or_default(),
                ),
                Route::Direct | Route::Gated => (CommitStatus::AbortedVersion, message.clone()),
            };
            FileResult {
                path: group.path.clone(),
                status,
                message,
            }
        })
        .collect();
    ChangesetResult {
        files,
        published_manifest_version: None,
        timings: std::collections::BTreeMap::new(),
    }
}

static AUTO_SQUASH_MAX_DEPTH_CONFIG: AtomicUsize = AtomicUsize::new(AUTO_SQUASH_MAX_DEPTH);

pub fn configure_auto_squash_max_depth(max_depth: usize) {
    AUTO_SQUASH_MAX_DEPTH_CONFIG.store(max_depth.max(1), Ordering::Relaxed);
}

fn auto_squash_max_depth() -> usize {
    AUTO_SQUASH_MAX_DEPTH_CONFIG.load(Ordering::Relaxed)
}

#[derive(Clone)]
pub struct CommitTransaction {
    pub root: PathBuf,
}

impl CommitTransactionPort for CommitTransaction {
    fn revalidate_and_publish(
        &self,
        combined: &PreparedChangeset,
    ) -> std::result::Result<ChangesetResult, PublishConflict> {
        let total_start = Instant::now();
        let mut stack = match LayerStack::open(self.root.clone()) {
            Ok(stack) => stack,
            Err(err) => return Ok(failed_revalidate_result(combined, &err, total_start)),
        };
        let validate_start = Instant::now();
        let active = match stack.read_active_manifest() {
            Ok(manifest) => manifest,
            Err(err) => return Ok(failed_revalidate_result(combined, &err, total_start)),
        };
        let view = MergedView::new(self.root.clone());
        let validations = validate_prepared(&self.root, &view, &active, combined);
        let validate_s = validate_start.elapsed().as_secs_f64();
        if combined.atomic
            && validations
                .iter()
                .any(|file| is_validation_failure(file.status))
        {
            return Ok(atomic_validation_drop_result(
                combined,
                validations,
                validate_s,
                total_start,
            ));
        }
        let publishable_changes = publishable_changes(combined, &validations);
        if publishable_changes.is_empty() {
            return Ok(no_publish_result(
                combined,
                validations,
                validate_s,
                total_start,
            ));
        }
        let publish_start = Instant::now();
        match stack.publish_layer(&publishable_changes) {
            Ok(manifest) => {
                let publish_s = publish_start.elapsed().as_secs_f64();
                let auto_squash_timings = run_auto_squash(&mut stack);
                Ok(committed_changeset_result(
                    combined,
                    validations,
                    manifest_version_u64_optional(manifest.version),
                    PublishedCommitTimings {
                        validate_s,
                        publish_s,
                        auto_squash_timings,
                        total_start,
                    },
                ))
            }
            Err(crate::LayerStackError::ManifestConflict { found, .. }) => Err(PublishConflict {
                observed_version: manifest_version_u64_optional(found),
            }),
            Err(err) => {
                let publish_s = publish_start.elapsed().as_secs_f64();
                let timings = commit_timings(
                    combined,
                    validate_s,
                    publish_s,
                    total_start.elapsed().as_secs_f64(),
                );
                Ok(failed_changeset_with_timings(
                    combined,
                    &err.to_string(),
                    timings,
                ))
            }
        }
    }
}

fn failed_revalidate_result(
    combined: &PreparedChangeset,
    err: &crate::LayerStackError,
    total_start: Instant,
) -> ChangesetResult {
    let timings = commit_timings(combined, 0.0, 0.0, total_start.elapsed().as_secs_f64());
    failed_changeset_with_timings(combined, &err.to_string(), timings)
}

fn atomic_validation_drop_result(
    combined: &PreparedChangeset,
    validations: Vec<FileResult>,
    validate_s: f64,
    total_start: Instant,
) -> ChangesetResult {
    ChangesetResult {
        files: validations
            .into_iter()
            .map(|file| {
                if file.status.is_published() {
                    FileResult {
                        status: CommitStatus::Dropped,
                        message: "not published because atomic changeset validation failed"
                            .to_owned(),
                        ..file
                    }
                } else {
                    file
                }
            })
            .collect(),
        published_manifest_version: None,
        timings: commit_timings(
            combined,
            validate_s,
            0.0,
            total_start.elapsed().as_secs_f64(),
        ),
    }
}

fn publishable_changes(
    combined: &PreparedChangeset,
    validations: &[FileResult],
) -> Vec<LayerChange> {
    let publishable_paths = validations
        .iter()
        .filter(|file| file.status.is_published())
        .map(|file| file.path.as_str())
        .collect::<HashSet<_>>();
    combined
        .changes
        .iter()
        .filter(|change| publishable_paths.contains(change.path().as_str()))
        .cloned()
        .collect()
}

fn no_publish_result(
    combined: &PreparedChangeset,
    validations: Vec<FileResult>,
    validate_s: f64,
    total_start: Instant,
) -> ChangesetResult {
    ChangesetResult {
        files: validations,
        published_manifest_version: None,
        timings: commit_timings(
            combined,
            validate_s,
            0.0,
            total_start.elapsed().as_secs_f64(),
        ),
    }
}

fn run_auto_squash(stack: &mut LayerStack) -> BTreeMap<String, f64> {
    let mut timings = BTreeMap::new();
    let Ok(active) = stack.read_active_manifest() else {
        return timings;
    };
    let max_depth = auto_squash_max_depth();
    if active.depth() <= max_depth
        || !stack
            .can_squash(max_depth)
            .is_ok_and(|can_squash| can_squash)
    {
        return timings;
    }

    let squash_start = Instant::now();
    let squashed = stack.squash(max_depth).ok().flatten();
    let squash_elapsed_s = squash_start.elapsed().as_secs_f64();
    timings.insert(
        "layer_stack.auto_squash.total_s".to_owned(),
        squash_elapsed_s,
    );
    timings.insert(
        "layer_stack.auto_squash.max_depth".to_owned(),
        usize_to_f64_saturating(max_depth),
    );
    timings.insert(
        "layer_stack.auto_squash.depth_before".to_owned(),
        usize_to_f64_saturating(active.depth()),
    );
    match squashed {
        Some(manifest) => {
            timings.insert(
                "layer_stack.auto_squash.depth_after".to_owned(),
                usize_to_f64_saturating(manifest.depth()),
            );
            timings.insert(
                "layer_stack.auto_squash.manifest_version".to_owned(),
                i64_to_f64_saturating(manifest.version),
            );
        }
        None => {
            timings.insert("layer_stack.auto_squash.raced".to_owned(), 1.0);
        }
    }
    timings
}

fn committed_changeset_result(
    combined: &PreparedChangeset,
    validations: Vec<FileResult>,
    published_manifest_version: Option<u64>,
    phases: PublishedCommitTimings,
) -> ChangesetResult {
    let mut timings = commit_timings(
        combined,
        phases.validate_s,
        phases.publish_s,
        phases.total_start.elapsed().as_secs_f64(),
    );
    timings.extend(phases.auto_squash_timings);
    ChangesetResult {
        files: validations
            .into_iter()
            .map(|file| {
                if file.status.is_published() {
                    FileResult {
                        status: CommitStatus::Committed,
                        ..file
                    }
                } else {
                    file
                }
            })
            .collect(),
        published_manifest_version,
        timings,
    }
}

struct PublishedCommitTimings {
    validate_s: f64,
    publish_s: f64,
    auto_squash_timings: BTreeMap<String, f64>,
    total_start: Instant,
}

fn validate_prepared(
    root: &Path,
    view: &MergedView,
    manifest: &Manifest,
    prepared: &PreparedChangeset,
) -> Vec<FileResult> {
    let mut parent_absent_cache = HashMap::new();
    prepared
        .path_groups
        .iter()
        .map(|group| match group.route {
            Route::Drop => FileResult {
                path: group.path.clone(),
                status: CommitStatus::Dropped,
                message: group
                    .message
                    .clone()
                    .unwrap_or_else(|| "change dropped".to_owned()),
            },
            Route::Reject => FileResult {
                path: group.path.clone(),
                status: CommitStatus::Rejected,
                message: group
                    .message
                    .clone()
                    .unwrap_or_else(|| "change rejected".to_owned()),
            },
            Route::Direct => validate_direct_group(&group.path),
            Route::Gated => validate_gated_group(
                root,
                view,
                manifest,
                &group.path,
                group.base_hash.as_deref(),
                &mut parent_absent_cache,
            ),
        })
        .collect()
}

fn validate_direct_group(path: &LayerPath) -> FileResult {
    FileResult {
        path: path.clone(),
        status: CommitStatus::Accepted,
        message: String::new(),
    }
}

fn validate_gated_group(
    root: &Path,
    view: &MergedView,
    manifest: &Manifest,
    path: &LayerPath,
    base_hash: Option<&str>,
    parent_absent_cache: &mut HashMap<String, bool>,
) -> FileResult {
    let path_str = path.as_str();
    if base_hash.is_none() {
        if let Some(parent) = parent_dir(path_str) {
            let parent_absent = *parent_absent_cache
                .entry(parent.to_owned())
                .or_insert_with(|| parent_absent_from_manifest(root, manifest, parent));
            if parent_absent {
                return FileResult {
                    path: path.clone(),
                    status: CommitStatus::Accepted,
                    message: String::new(),
                };
            }
        }
    }
    match view.read_bytes(path_str, manifest) {
        Ok((bytes, exists)) if hash_current(bytes.as_deref(), exists).as_deref() == base_hash => {
            FileResult {
                path: path.clone(),
                status: CommitStatus::Accepted,
                message: String::new(),
            }
        }
        Ok(_) => FileResult {
            path: path.clone(),
            status: CommitStatus::AbortedVersion,
            message: "content changed".to_owned(),
        },
        Err(err) => FileResult {
            path: path.clone(),
            status: CommitStatus::Failed,
            message: err.to_string(),
        },
    }
}

fn parent_dir(path: &str) -> Option<&str> {
    path.rsplit_once('/')
        .map(|(parent, _)| parent)
        .filter(|parent| !parent.is_empty())
}

fn parent_absent_from_manifest(root: &Path, manifest: &Manifest, parent: &str) -> bool {
    manifest.layers.iter().all(|layer| {
        let path = PathBuf::from(&layer.path);
        let layer_dir = if path.is_absolute() {
            path
        } else {
            root.join(path)
        };
        matches!(
            std::fs::symlink_metadata(layer_dir.join(parent)),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound
        )
    })
}

const fn is_validation_failure(status: CommitStatus) -> bool {
    matches!(
        status,
        CommitStatus::AbortedOverlap
            | CommitStatus::AbortedVersion
            | CommitStatus::Failed
            | CommitStatus::Rejected
    )
}

fn failed_changeset_with_timings(
    prepared: &PreparedChangeset,
    message: &str,
    timings: BTreeMap<String, f64>,
) -> ChangesetResult {
    ChangesetResult {
        files: prepared
            .path_groups
            .iter()
            .map(|group| FileResult {
                path: group.path.clone(),
                status: CommitStatus::Failed,
                message: message.to_owned(),
            })
            .collect(),
        published_manifest_version: None,
        timings,
    }
}

fn commit_timings(
    prepared: &PreparedChangeset,
    validate_s: f64,
    publish_s: f64,
    total_s: f64,
) -> BTreeMap<String, f64> {
    let mut timings = BTreeMap::new();
    timings.insert("occ.apply.total_s".to_owned(), total_s);
    timings.insert("occ.commit.total_s".to_owned(), total_s);
    timings.insert("occ.commit.validate_groups_s".to_owned(), validate_s);
    timings.insert("occ.commit.publish_layer_s".to_owned(), publish_s);
    timings.insert(
        "occ.commit.stager_write_count".to_owned(),
        usize_to_f64_saturating(prepared.changes.len()),
    );
    timings.insert("occ.commit.stager_write_total_s".to_owned(), publish_s);
    timings.insert(
        "occ.commit.gated_path_count".to_owned(),
        usize_to_f64_saturating(
            prepared
                .path_groups
                .iter()
                .filter(|group| group.route == Route::Gated)
                .count(),
        ),
    );
    timings.insert(
        "occ.commit.direct_path_count".to_owned(),
        usize_to_f64_saturating(
            prepared
                .path_groups
                .iter()
                .filter(|group| group.route == Route::Direct)
                .count(),
        ),
    );
    for key in [
        "occ.commit.gated_read_current_total_s",
        "occ.commit.gated_apply_changes_total_s",
        "occ.commit.gated_stage_delta_total_s",
        "occ.commit.direct_read_current_total_s",
        "occ.commit.direct_apply_changes_total_s",
        "occ.commit.direct_stage_delta_total_s",
    ] {
        timings.insert(key.to_owned(), 0.0);
    }
    timings
}

fn manifest_version_u64_optional(version: i64) -> Option<u64> {
    u64::try_from(version).ok()
}


#[cfg(test)]
#[path = "../../tests/unit/commit/queue.rs"]
mod queue_tests;
#[cfg(test)]
#[path = "../../tests/unit/commit/transaction.rs"]
mod transaction_tests;
