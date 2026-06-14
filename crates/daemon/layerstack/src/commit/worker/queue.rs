use std::collections::HashSet;
use std::sync::{mpsc, Mutex};
use std::time::Duration;

use serde_json::json;

use super::super::{
    ChangesetResult, CommitError, CommitStatus, FileResult, OccTraceEvent, PublishDecision, Route,
};
use super::transaction::{commit_timings, CommitTransaction};

pub(crate) const COMMIT_QUEUE_THREAD_NAME: &str = "occ-commit-queue";

pub(crate) const MAX_BATCH_SIZE: usize = 64;

pub(crate) const BATCH_WINDOW_S: f64 = 0.002;

pub(crate) const MAX_OCC_CAS_RETRIES: u32 = 3;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(in crate::commit) struct PreparedChangeset {
    pub(in crate::commit) path_groups: Vec<PublishDecision>,
    pub(in crate::commit) changes: Vec<crate::model::LayerChange>,
    pub(in crate::commit) atomic: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(in crate::commit) struct PublishConflict {
    pub(in crate::commit) observed_version: Option<u64>,
}

pub(super) struct WorkItem {
    pub(super) prepared: PreparedChangeset,
    pub(super) reply: mpsc::Sender<Result<ChangesetResult, CommitError>>,
}

enum QueueItem {
    Work(WorkItem),
    Stop,
}

pub(in crate::commit) struct CommitQueue {
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
    pub(in crate::commit) fn new(transaction: CommitTransaction) -> Self {
        let (sender, receiver) = mpsc::channel();
        Self {
            sender,
            receiver: Mutex::new(Some(receiver)),
            transaction: Mutex::new(Some(transaction)),
            handle: None,
            closed: false,
        }
    }

    pub(in crate::commit) fn start(&mut self) -> Result<(), CommitError> {
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

    pub(in crate::commit) fn close(&mut self) -> Result<(), CommitError> {
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

    pub(in crate::commit) fn submit(
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

#[cfg(test)]
#[path = "../../../tests/unit/commit/queue.rs"]
mod queue_tests;
