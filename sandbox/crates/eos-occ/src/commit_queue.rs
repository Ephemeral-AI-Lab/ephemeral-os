//! The single-writer publish queue — the MF-1 invariant in code.
//!
//! Exactly one `occ-commit-queue` writer serializes every publish for a given
//! `layer_stack_root`. N disjoint file-API writes are batched into ONE manifest
//! CAS attempt; on a stale base the publisher returns a conflict and the writer
//! retries up to [`MAX_OCC_CAS_RETRIES`] times before surfacing
//! [`OccStatus::AbortedVersion`](crate::OccStatus::AbortedVersion) on every
//! path.
//!
//! ## MF-1: a SINGLE writer, no second instance
//! Any second OCC entry point (notably the PPC self-managed plugin callback)
//! MUST route through THIS same writer + the storage lease keyed by
//! `layer_stack_root`. A second [`CommitQueue`] for the same root would race the
//! manifest CAS and break linearizability — the per-root services singleton
//! (eos-daemon) is what guarantees one queue per root.
//!
//! ## Threading model (per RUST-GUIDANCE §5)
//! The Python uses `threading.Thread` + `queue.Queue` + `concurrent.futures`,
//! NOT asyncio, for the queue itself (eos-occ has no tokio dep). The Rust port
//! is an `mpsc` work queue with one dedicated consumer thread named
//! `occ-commit-queue`; each work item carries a `std::sync::mpsc` reply sender
//! (the std analogue of a `oneshot`) so the async daemon can await the result
//! without the OCC crate ever holding a lock across `.await`.

use std::sync::mpsc;

use eos_protocol::LayerChange;

use crate::error::OccError;
use crate::route::{ChangesetResult, PublishDecision};

/// Dedicated single-writer thread name (reproduce exactly).
// PORT backend/src/sandbox/occ/commit_queue.py:90 — Thread(name="occ-commit-queue")
pub const COMMIT_QUEUE_THREAD_NAME: &str = "occ-commit-queue";

/// Default upper bound on changesets coalesced into one CAS attempt.
// PORT backend/src/sandbox/occ/commit_queue.py:66 — max_batch_size: int = 64
pub const MAX_BATCH_SIZE: usize = 64;

/// Default batch-coalescing window in seconds (2 ms).
///
/// Only paid when a non-blocking drain emptied the queue AND batch headroom
/// remains; otherwise it is dead wall-clock on the single-commit hot path.
// PORT backend/src/sandbox/occ/commit_queue.py:67 — batch_window_s: float = 0.002
pub const BATCH_WINDOW_S: f64 = 0.002;

/// Bounded CAS-mismatch retry budget before `AbortedVersion`.
// PORT backend/src/sandbox/occ/commit_queue.py:27 — MAX_OCC_CAS_RETRIES: int = 3
pub const MAX_OCC_CAS_RETRIES: u32 = 3;

/// A routed changeset ready for the publish transaction.
///
/// One [`PublishDecision`] per disjoint normalized path plus the typed changes
/// to apply; `atomic` requires every path to validate before any path lands.
/// The `snapshot_version` pins the base the CAS check revalidates against.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreparedChangeset {
    /// Base manifest version this changeset was prepared against (`None` =
    /// empty root).
    pub snapshot_version: Option<u64>,
    /// Disjoint per-path route decisions.
    pub path_groups: Vec<PublishDecision>,
    /// Typed mutations (CAS-hashed by the layer-stack publisher).
    pub changes: Vec<LayerChange>,
    /// All-or-nothing publish (Python default `True`).
    pub atomic: bool,
}

/// The publish-transaction half of the layer-stack port the queue drives.
///
/// Defined here as a local placeholder (the real port + adapter live in
/// `eos-layerstack`; do NOT import sibling items still being written). The
/// daemon injects an implementation that revalidates the CAS base and publishes
/// a new manifest version, returning [`PublishConflict`] on a stale base.
// PORT backend/src/sandbox/occ/commit_transaction.py — CommitTransaction.revalidate_and_publish
pub trait CommitTransactionPort: Send {
    /// Revalidate the base hash and atomically publish, or signal a CAS
    /// conflict so the queue can retry.
    fn revalidate_and_publish(
        &self,
        combined: &PreparedChangeset,
    ) -> Result<ChangesetResult, PublishConflict>;
}

/// Signals a manifest CAS mismatch (`ManifestConflictError`) so the writer
/// retries against the fresh base.
// PORT backend/src/sandbox/layer_stack/manifest.py — ManifestConflictError
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PublishConflict {
    /// The base version the publisher actually observed.
    pub observed_version: Option<u64>,
}

/// One unit of work on the single-writer queue: a prepared changeset plus the
/// reply channel the submitter awaits.
struct WorkItem {
    prepared: PreparedChangeset,
    reply: mpsc::Sender<Result<ChangesetResult, OccError>>,
}

/// Either real work or the stop sentinel that drains and exits the worker.
enum QueueItem {
    Work(WorkItem),
    Stop,
}

/// Serializes OCC publishes while batching disjoint prepared changesets.
///
/// Owns the `mpsc` producer half; the consumer half is moved into the spawned
/// `occ-commit-queue` thread on [`CommitQueue::start`].
pub struct CommitQueue<T: CommitTransactionPort + 'static> {
    sender: mpsc::Sender<QueueItem>,
    receiver: Option<mpsc::Receiver<QueueItem>>,
    transaction: Option<T>,
    handle: Option<std::thread::JoinHandle<()>>,
    max_batch_size: usize,
    batch_window_s: f64,
    max_cas_retries: u32,
    closed: bool,
}

impl<T: CommitTransactionPort + 'static> CommitQueue<T> {
    /// Build a queue with default batching/retry tuning.
    pub fn new(transaction: T) -> Self {
        Self::with_config(
            transaction,
            MAX_BATCH_SIZE,
            BATCH_WINDOW_S,
            MAX_OCC_CAS_RETRIES,
        )
    }

    /// Build a queue with explicit batching/retry tuning.
    ///
    /// Clamps to match Python: `max_batch_size >= 1`, `batch_window_s >= 0.0`,
    /// `max_cas_retries >= 1`.
    // PORT backend/src/sandbox/occ/commit_queue.py:66-74 — __init__ clamps
    pub fn with_config(
        transaction: T,
        max_batch_size: usize,
        batch_window_s: f64,
        max_cas_retries: u32,
    ) -> Self {
        let (sender, receiver) = mpsc::channel();
        Self {
            sender,
            receiver: Some(receiver),
            transaction: Some(transaction),
            handle: None,
            max_batch_size: max_batch_size.max(1),
            batch_window_s: batch_window_s.max(0.0),
            max_cas_retries: max_cas_retries.max(1),
            closed: false,
        }
    }

    /// Spawn the single `occ-commit-queue` consumer thread.
    // PORT backend/src/sandbox/occ/commit_queue.py:90 — Thread(target=_run, name="occ-commit-queue", daemon=True)
    pub fn start(&mut self) -> Result<(), OccError> {
        let _ = (
            &self.receiver,
            &self.transaction,
            &mut self.handle,
            self.max_batch_size,
            self.batch_window_s,
            self.max_cas_retries,
        );
        todo!("PORT occ/commit_queue.py:90 — spawn the named single-writer worker thread")
    }

    /// Stop the worker after pending queued work drains.
    // PORT backend/src/sandbox/occ/commit_queue.py — close(): put _STOP then join
    pub fn close(&mut self) -> Result<(), OccError> {
        let _ = (&self.sender, &mut self.handle, &mut self.closed);
        todo!("PORT occ/commit_queue.py — enqueue _STOP sentinel and join the worker")
    }

    /// Enqueue a prepared changeset and return a reply receiver to await on.
    ///
    /// The reply channel is the std analogue of a tokio `oneshot`; the async
    /// daemon awaits it off-thread without the queue holding any lock across
    /// `.await` (RUST-GUIDANCE §5).
    // PORT backend/src/sandbox/occ/commit_queue.py:108-124 — submit(): future + enqueue
    pub fn submit(
        &self,
        prepared: PreparedChangeset,
    ) -> Result<mpsc::Receiver<Result<ChangesetResult, OccError>>, OccError> {
        let _ = (&self.sender, prepared, self.closed);
        todo!("PORT occ/commit_queue.py:108 — enqueue _WorkItem(prepared, reply, enqueued_at)")
    }

    /// Consumer loop: block for the first item, non-blocking-drain the rest,
    /// pay the batch window only with headroom, then commit disjoint batches.
    // PORT backend/src/sandbox/occ/commit_queue.py:131 — _run() consumer loop
    fn run(
        _receiver: mpsc::Receiver<QueueItem>,
        _transaction: T,
        _max_batch_size: usize,
        _batch_window_s: f64,
        _max_cas_retries: u32,
    ) {
        todo!("PORT occ/commit_queue.py:131 — first-blocking + drain + batch-window + commit_batch")
    }

    /// Commit one disjoint batch with the bounded CAS-retry loop, fanning each
    /// path's [`FileResult`](crate::FileResult) back to its submitter.
    // PORT backend/src/sandbox/occ/commit_queue.py:168 — _commit_batch(): retry + fan-out
    fn commit_batch(_transaction: &T, _batch: Vec<WorkItem>, _max_cas_retries: u32) {
        todo!("PORT occ/commit_queue.py:168 — revalidate_and_publish retry loop + per-item reply")
    }
}
