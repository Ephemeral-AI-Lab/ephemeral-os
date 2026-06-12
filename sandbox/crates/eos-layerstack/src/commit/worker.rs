use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{mpsc, Mutex};
use std::time::{Duration, Instant};

use serde_json::json;

use crate::fs::resolve_layer_path;
use crate::model::{LayerChange, LayerPath, Manifest};
use crate::{LayerStack, MergedView, AUTO_SQUASH_MAX_DEPTH};

use super::{
    hash_current, ChangesetResult, CommitError, CommitStatus, FileResult, OccTraceEvent,
    PublishDecision, Route,
};

pub(crate) const COMMIT_QUEUE_THREAD_NAME: &str = "occ-commit-queue";

pub(crate) const MAX_BATCH_SIZE: usize = 64;

pub(crate) const BATCH_WINDOW_S: f64 = 0.002;

pub(crate) const MAX_OCC_CAS_RETRIES: u32 = 3;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct PreparedChangeset {
    pub(super) path_groups: Vec<PublishDecision>,
    pub(super) changes: Vec<LayerChange>,
    pub(super) atomic: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct PublishConflict {
    pub(super) observed_version: Option<u64>,
}

struct WorkItem {
    prepared: PreparedChangeset,
    reply: mpsc::Sender<Result<ChangesetResult, CommitError>>,
}

enum QueueItem {
    Work(WorkItem),
    Stop,
}

pub(super) struct CommitQueue {
    sender: mpsc::Sender<QueueItem>,
    receiver: Mutex<Option<mpsc::Receiver<QueueItem>>>,
    transaction: Mutex<Option<CommitTransaction>>,
    handle: Option<std::thread::JoinHandle<()>>,
    closed: bool,
}

struct CommitWorker {
    receiver: mpsc::Receiver<QueueItem>,
    transaction: CommitTransaction,
}

impl CommitQueue {
    pub(super) fn new(transaction: CommitTransaction) -> Self {
        let (sender, receiver) = mpsc::channel();
        Self {
            sender,
            receiver: Mutex::new(Some(receiver)),
            transaction: Mutex::new(Some(transaction)),
            handle: None,
            closed: false,
        }
    }

    pub(super) fn start(&mut self) -> Result<(), CommitError> {
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

    pub(super) fn close(&mut self) -> Result<(), CommitError> {
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

    pub(super) fn submit(
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
            .send(QueueItem::Work(WorkItem { prepared, reply }))
            .map_err(|_| CommitError::QueueClosed)?;
        Ok(receiver)
    }
}

impl CommitWorker {
    fn run(self) {
        while let Ok(first) = self.receiver.recv() {
            let QueueItem::Work(first) = first else {
                return;
            };
            let mut items = vec![first];
            let mut stop_seen = drain_ready(&self.receiver, &mut items, MAX_BATCH_SIZE);
            if !stop_seen && items.len() < MAX_BATCH_SIZE {
                std::thread::sleep(Duration::from_secs_f64(BATCH_WINDOW_S));
                stop_seen = drain_ready(&self.receiver, &mut items, MAX_BATCH_SIZE);
            }
            for batch in disjoint_batches(items) {
                commit_batch(&self.transaction, batch);
            }
            if stop_seen {
                return;
            }
        }
    }
}

fn commit_batch(transaction: &CommitTransaction, batch: Vec<WorkItem>) {
    let Some(combined) = combine_prepared(batch.iter().map(|item| &item.prepared)) else {
        return;
    };
    let mut attempts = 0;
    let mut result = loop {
        match transaction.revalidate_and_publish(&combined) {
            Ok(result) => break result,
            Err(conflict) => {
                attempts += 1;
                if attempts >= MAX_OCC_CAS_RETRIES {
                    break cas_exhaustion_result(&combined, &conflict, MAX_OCC_CAS_RETRIES);
                }
            }
        }
    };
    result.events.insert(
        0,
        OccTraceEvent::new(
            "occ",
            "worker_batch_finished",
            json!({
                "batch_item_count": batch.len(),
                "combined_path_count": combined.path_groups.len(),
                "combined_change_count": combined.changes.len(),
                "atomic": combined.atomic,
                "cas_retry_count": attempts,
            }),
        ),
    );
    for item in batch {
        let files = result_files_for_item(&result, &item.prepared);
        let _ = item.reply.send(Ok(ChangesetResult {
            files,
            published_manifest_version: result.published_manifest_version,
            timings: result.timings.clone(),
            events: result.events.clone(),
        }));
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
                Route::Direct | Route::Gated => (CommitStatus::AbortedVersion, message.clone()),
            };
            let observed_version = if group.route == Route::Drop {
                None
            } else {
                conflict.observed_version
            };
            let observed_state = if group.route == Route::Drop {
                None
            } else {
                Some("manifest_conflict".to_owned())
            };
            FileResult {
                path: group.path.clone(),
                status,
                message,
                observed_version,
                observed_state,
            }
        })
        .collect();
    ChangesetResult {
        files,
        published_manifest_version: None,
        timings: commit_timings(prepared, 0.0, 0.0, 0.0),
        events: Vec::new(),
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
pub(super) struct CommitTransaction {
    pub(super) root: PathBuf,
}

impl CommitTransaction {
    pub(super) fn revalidate_and_publish(
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
        let active_lease_count = stack.active_lease_count();
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
                let auto_squash = run_auto_squash(&mut stack);
                Ok(committed_changeset_result(
                    combined,
                    validations,
                    manifest_version_u64_optional(manifest.version),
                    &active,
                    active_lease_count,
                    &manifest,
                    validate_s,
                    publish_s,
                    auto_squash,
                    total_start,
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
        events: Vec::new(),
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
        events: Vec::new(),
    }
}

struct AutoSquashTrace {
    timings: BTreeMap<String, f64>,
    events: Vec<OccTraceEvent>,
}

fn run_auto_squash(stack: &mut LayerStack) -> AutoSquashTrace {
    let mut timings = BTreeMap::new();
    let max_depth = auto_squash_max_depth();
    let (depth_before, decision) = match stack.squash_plan_decision(max_depth, 2) {
        Ok(decision) => decision,
        Err(err) => {
            return AutoSquashTrace {
                timings,
                events: vec![auto_squash_event(
                    "auto_squash_skipped",
                    json!({
                        "reason": "plan_failed",
                        "error": err.to_string(),
                        "max_depth": max_depth,
                    }),
                )],
            };
        }
    };
    if let Some(reason) = decision.skip_reason {
        return AutoSquashTrace {
            timings,
            events: vec![auto_squash_event(
                "auto_squash_skipped",
                json!({
                    "reason": reason.as_str(),
                    "max_depth": max_depth,
                    "depth_before": depth_before,
                }),
            )],
        };
    }

    let mut events = vec![auto_squash_event(
        "auto_squash_started",
        json!({
            "max_depth": max_depth,
            "depth_before": depth_before,
        }),
    )];
    let squash_start = Instant::now();
    let squashed = stack.squash(max_depth);
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
        usize_to_f64_saturating(depth_before),
    );
    match squashed {
        Ok(outcome) => {
            let Some(manifest) = outcome.manifest else {
                timings.insert("layer_stack.auto_squash.raced".to_owned(), 1.0);
                events.push(auto_squash_event(
                    "auto_squash_skipped",
                    json!({
                        "reason": "live_prefix_race",
                        "max_depth": max_depth,
                        "depth_before": depth_before,
                        "duration_s": squash_elapsed_s,
                    }),
                ));
                return AutoSquashTrace { timings, events };
            };
            timings.insert(
                "layer_stack.auto_squash.depth_after".to_owned(),
                usize_to_f64_saturating(manifest.depth()),
            );
            timings.insert(
                "layer_stack.auto_squash.manifest_version".to_owned(),
                i64_to_f64_saturating(manifest.version),
            );
            events.push(auto_squash_event(
                "auto_squash_finished",
                json!({
                    "success": true,
                    "max_depth": max_depth,
                    "depth_before": depth_before,
                    "depth_after": manifest.depth(),
                    "manifest_version": manifest.version,
                    "duration_s": squash_elapsed_s,
                    "lease_release_error": outcome
                        .lease_release_error
                        .as_ref()
                        .map(ToString::to_string),
                }),
            ));
            if let Some(release_error) = outcome.lease_release_error {
                events.push(auto_squash_event(
                    "lease_release_failed",
                    json!({
                        "lease_owner": "auto_squash",
                        "reason": "post_commit_release_failed",
                        "error": release_error.to_string(),
                        "manifest_version": manifest.version,
                    }),
                ));
            }
            AutoSquashTrace { timings, events }
        }
        Err(err) => {
            events.push(auto_squash_event(
                "auto_squash_finished",
                json!({
                    "success": false,
                    "error": err.to_string(),
                    "max_depth": max_depth,
                    "depth_before": depth_before,
                    "duration_s": squash_elapsed_s,
                }),
            ));
            AutoSquashTrace { timings, events }
        }
    }
}

fn auto_squash_event(name: &'static str, details: serde_json::Value) -> OccTraceEvent {
    OccTraceEvent::new("layer_stack", name, details)
}

fn committed_changeset_result(
    combined: &PreparedChangeset,
    validations: Vec<FileResult>,
    published_manifest_version: Option<u64>,
    active_manifest: &Manifest,
    active_lease_count: usize,
    published_manifest: &Manifest,
    validate_s: f64,
    publish_s: f64,
    auto_squash: AutoSquashTrace,
    total_start: Instant,
) -> ChangesetResult {
    let mut timings = commit_timings(
        combined,
        validate_s,
        publish_s,
        total_start.elapsed().as_secs_f64(),
    );
    timings.extend(auto_squash.timings);
    let mut events = vec![
        OccTraceEvent::new(
            "layer_stack",
            "manifest_validated",
            json!({
                "manifest_version": active_manifest.version,
                "manifest_depth": active_manifest.depth(),
                "manifest_path_count": active_manifest.layers.len(),
                "active_lease_count": active_lease_count,
            }),
        ),
        OccTraceEvent::new(
            "layer_stack",
            "publish_layer_finished",
            json!({
                "success": true,
                "manifest_version_before": active_manifest.version,
                "manifest_version_after": published_manifest.version,
                "published_manifest_version": published_manifest_version,
                "published_layer_count": published_manifest.layers.len().saturating_sub(active_manifest.layers.len()),
                "duration_s": publish_s,
            }),
        ),
    ];
    events.extend(auto_squash.events);
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
        events,
    }
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
                observed_version: None,
                observed_state: None,
            },
            Route::Direct => accepted_file(&group.path),
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

fn accepted_file(path: &LayerPath) -> FileResult {
    FileResult {
        path: path.clone(),
        status: CommitStatus::Accepted,
        message: String::new(),
        observed_version: None,
        observed_state: None,
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
                return accepted_file(path);
            }
        }
    }
    match view.read_bytes(path_str, manifest) {
        Ok((bytes, exists)) if hash_current(bytes.as_deref(), exists).as_deref() == base_hash => {
            accepted_file(path)
        }
        Ok(_) => FileResult {
            path: path.clone(),
            status: CommitStatus::AbortedVersion,
            message: "content changed".to_owned(),
            observed_version: None,
            observed_state: Some("content_changed".to_owned()),
        },
        Err(err) => FileResult {
            path: path.clone(),
            status: CommitStatus::Failed,
            message: err.to_string(),
            observed_version: None,
            observed_state: Some("read_failed".to_owned()),
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
        let layer_dir = resolve_layer_path(root, &layer.path);
        matches!(
            std::fs::symlink_metadata(layer_dir.join(parent)),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound
        )
    })
}

const fn is_validation_failure(status: CommitStatus) -> bool {
    !status.is_success()
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
                observed_version: None,
                observed_state: Some("storage_error".to_owned()),
            })
            .collect(),
        published_manifest_version: None,
        timings,
        events: Vec::new(),
    }
}

fn commit_timings(
    prepared: &PreparedChangeset,
    validate_s: f64,
    publish_s: f64,
    total_s: f64,
) -> BTreeMap<String, f64> {
    let mut timings = BTreeMap::new();
    timings.insert("occ.commit.total_s".to_owned(), total_s);
    timings.insert("occ.commit.validate_groups_s".to_owned(), validate_s);
    timings.insert("occ.commit.publish_layer_s".to_owned(), publish_s);
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
    timings
}

fn manifest_version_u64_optional(version: i64) -> Option<u64> {
    u64::try_from(version).ok()
}

fn usize_to_f64_saturating(value: usize) -> f64 {
    u32::try_from(value).map_or_else(|_| f64::from(u32::MAX), f64::from)
}

fn i64_to_f64_saturating(value: i64) -> f64 {
    u64::try_from(value).map_or(0.0, |value| {
        u32::try_from(value).map_or_else(|_| f64::from(u32::MAX), f64::from)
    })
}

#[cfg(test)]
#[path = "../../tests/unit/commit/queue.rs"]
mod queue_tests;
#[cfg(test)]
#[path = "../../tests/unit/commit/transaction.rs"]
mod transaction_tests;
