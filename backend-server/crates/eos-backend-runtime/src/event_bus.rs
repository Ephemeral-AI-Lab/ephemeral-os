//! [`EventBus`] — replay-safe milestone streaming off agent-core's synchronous
//! [`StreamEvent`] callback.
//!
//! agent-core emits stream events through a borrowing, synchronous callback
//! (`Arc<dyn Fn(&StreamEvent)>`). The backend must persist milestones to
//! `event_log` and fan them out to SSE/WebSocket subscribers **without** doing any
//! async I/O or holding an async lock inside that callback (AC5). The flow:
//!
//! 1. The sync callback serializes the event, classifies it (milestones only —
//!    high-volume deltas are dropped), reserves a per-request monotonic `seq`,
//!    and `try_send`s an owned [`EventRecord`] into a bounded channel. No `.await`,
//!    no lock. On a full queue it bumps a dropped counter and arms a gap marker.
//! 2. A per-request async **drainer** persists each record to `event_log`, then —
//!    and only then — broadcasts it (persist-before-broadcast). It coalesces armed
//!    drops into one durable [`EVENT_STREAM_GAP`] marker so milestone loss is
//!    visible in `/events` and the live stream, never silent.
//! 3. [`EventBus::subscribe`] replays `event_log` from `last_seq` and joins the
//!    live broadcast with **no gap at the handoff**: it subscribes live *before*
//!    reading replay and dedups by `seq`, and it recovers broadcast lag from the
//!    durable log rather than skipping.
//!
//! Lifecycle: the callback owns the only `mpsc::Sender`, so when the run drops its
//! callback (completion or cancel) the channel closes and the drainer exits.
//! [`EventBus::finish`] drops the bus's stream handle; once the drainer has also
//! exited, the broadcast sender is freed and live subscribers observe close (after
//! one final durable sweep). `event_log` rows stay durable for later replay.

use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicBool, AtomicI64, AtomicU64, Ordering};
use std::sync::Arc;

use parking_lot::Mutex;
use tokio::sync::{broadcast, mpsc};

use eos_backend_store::{EventLogRepo, StoreError};
use eos_backend_types::{EventRecord, EVENT_STREAM_GAP};
use eos_runtime::EventCallback;
use eos_types::{RequestId, UtcDateTime};

/// Default bound on the callback→drainer queue (records in flight before overflow
/// arms a gap marker).
const DEFAULT_QUEUE_CAPACITY: usize = 1024;
/// Default bound on the drainer→subscriber broadcast buffer (a slow subscriber
/// past this lags and recovers from the durable log).
const DEFAULT_LIVE_CAPACITY: usize = 1024;

/// Per-request streaming state shared by the sync callback, the async drainer, and
/// `subscribe`. The callback touches only atomics + the mpsc sender (held in the
/// closure, not here); this holds the broadcast sender plus the loss accounting.
#[derive(Debug)]
struct RequestStream {
    /// Per-request monotonic sequence source. First reserved seq is `1`, matching
    /// `EventLogRepo::list_since(.., 0)` replay-from-start (sequences begin at 1).
    seq: AtomicI64,
    /// Set by the callback when a `try_send`/persist drop occurs; the drainer
    /// coalesces it into one gap marker.
    gap_pending: AtomicBool,
    /// Cumulative dropped-milestone count (stamped into the gap marker payload).
    dropped: AtomicU64,
    /// Live fan-out to subscribers. The drainer is the only sender.
    live: broadcast::Sender<EventRecord>,
}

impl RequestStream {
    /// Reserve the next sequence (`1`-based, strictly increasing, unique even
    /// under concurrent callers).
    ///
    /// ORDERING PRECONDITION: the subscriber dedup ([`EventSubscription`]) assumes
    /// records are *persisted in `seq` order*. That holds because seq is reserved
    /// here in callback-invocation order, sent to the mpsc in that order, and a
    /// single drainer persists in receive order. The link in the chain is that the
    /// callback is invoked **sequentially per request** — true today: `run_request`
    /// passes `on_event` only to the root run, whose engine loop emits one event at
    /// a time (`agent_loop`), and subagent runs pass `None`. If a future change ever
    /// invokes one request's callback concurrently, seq uniqueness still holds but
    /// persist order may diverge from seq order, and the high-water dedup below
    /// would need to become seen-seq-set based (or seq must be stamped at persist
    /// time). See [`EventSubscription`].
    fn next_seq(&self) -> i64 {
        self.seq.fetch_add(1, Ordering::Relaxed) + 1
    }

    /// Record a dropped milestone and arm the gap marker. `Release` pairs with the
    /// drainer's `AcqRel` swap so the bumped `dropped` count is visible to it.
    fn note_drop(&self) {
        self.dropped.fetch_add(1, Ordering::Relaxed);
        self.gap_pending.store(true, Ordering::Release);
    }
}

/// Backend event bus: owns the durable `event_log` writer and the per-request live
/// streams. Share one instance (e.g. `Arc<EventBus>`) between the launcher (which
/// registers a callback per run) and the streaming API (which subscribes).
#[derive(Debug)]
pub struct EventBus {
    streams: Mutex<HashMap<RequestId, Arc<RequestStream>>>,
    event_log: EventLogRepo,
    queue_capacity: usize,
    live_capacity: usize,
}

impl EventBus {
    /// Build a bus over the durable `event_log` repository with default capacities.
    #[must_use]
    pub fn new(event_log: EventLogRepo) -> Self {
        Self::with_capacity(event_log, DEFAULT_QUEUE_CAPACITY, DEFAULT_LIVE_CAPACITY)
    }

    /// Build a bus with explicit queue/broadcast bounds (used by tests to force
    /// overflow and lag deterministically). Both bounds are floored at 1.
    #[must_use]
    pub fn with_capacity(event_log: EventLogRepo, queue_capacity: usize, live_capacity: usize) -> Self {
        Self {
            streams: Mutex::new(HashMap::new()),
            event_log,
            queue_capacity: queue_capacity.max(1),
            live_capacity: live_capacity.max(1),
        }
    }

    /// Register a run and return the synchronous engine callback for it. Spawns the
    /// per-request drainer; must be called within a Tokio runtime.
    ///
    /// The returned [`EventCallback`] owns the only `mpsc::Sender`, so dropping it
    /// (when the run ends) closes the queue and the drainer exits.
    #[must_use]
    pub fn register(&self, request_id: &RequestId) -> EventCallback {
        let (tx, stream) = self.open_stream(request_id);
        let request_id = request_id.clone();
        // The closure's `event` is inferred as `&StreamEvent` from the
        // `EventCallback` target, so we never name the engine event type. Body is
        // strictly synchronous: serialize, then run the shared enqueue path
        // (classify, reserve seq, `try_send`) — no `.await`, no lock.
        Arc::new(move |event| {
            let Ok(payload) = serde_json::to_value(event) else {
                return;
            };
            classify_and_enqueue(&tx, &stream, &request_id, payload);
        })
    }

    /// Create the channels + per-request [`RequestStream`], register it, and spawn
    /// the drainer. Production [`register`](Self::register) wraps the returned
    /// sender in the `StreamEvent` callback; tests drive the same enqueue path with
    /// pre-serialized payloads (a `#[non_exhaustive]` `StreamEvent` cannot be built
    /// outside `eos-engine`).
    fn open_stream(&self, request_id: &RequestId) -> (mpsc::Sender<EventRecord>, Arc<RequestStream>) {
        let (tx, rx) = mpsc::channel::<EventRecord>(self.queue_capacity);
        let (live, _) = broadcast::channel::<EventRecord>(self.live_capacity);
        let stream = Arc::new(RequestStream {
            seq: AtomicI64::new(0),
            gap_pending: AtomicBool::new(false),
            dropped: AtomicU64::new(0),
            live,
        });
        self.streams.lock().insert(request_id.clone(), stream.clone());
        tokio::spawn(drain(
            rx,
            stream.clone(),
            self.event_log.clone(),
            request_id.clone(),
        ));
        (tx, stream)
    }

    /// Subscribe to a request's stream, replaying persisted records with
    /// `seq > after_seq` then joining the live broadcast with no handoff gap.
    ///
    /// # Errors
    /// [`StoreError`] if the initial `event_log` replay read fails.
    pub async fn subscribe(
        &self,
        request_id: &RequestId,
        after_seq: i64,
    ) -> Result<EventSubscription, StoreError> {
        // Subscribe to live FIRST (before the replay read) so any record persisted
        // and broadcast during the read is captured by `live` rather than lost
        // between replay and live. The guard is dropped before the `.await`.
        let live = {
            let streams = self.streams.lock();
            streams.get(request_id).map(|s| s.live.subscribe())
        };
        let replay = self.event_log.list_since(request_id, after_seq).await?;
        Ok(EventSubscription {
            request_id: request_id.clone(),
            event_log: self.event_log.clone(),
            replay: replay.into(),
            live,
            last_seq: after_seq,
        })
    }

    /// Drop the bus's handle on a finished run's stream. Combined with the run
    /// dropping its callback (which closes the queue and exits the drainer), this
    /// frees the broadcast sender so live subscribers observe close. Durable
    /// `event_log` rows remain replayable.
    pub fn finish(&self, request_id: &RequestId) {
        self.streams.lock().remove(request_id);
    }

    /// The cumulative dropped-milestone count for a live run, or `0` if unknown.
    #[must_use]
    pub fn dropped_count(&self, request_id: &RequestId) -> u64 {
        self.streams
            .lock()
            .get(request_id)
            .map_or(0, |s| s.dropped.load(Ordering::Relaxed))
    }
}

/// The callback's synchronous core: classify the serialized event, and for a
/// milestone reserve a `seq`, build the owned [`EventRecord`], and `try_send` it.
/// A full queue arms a gap marker instead of blocking. No `.await`, no lock — this
/// is the AC5 hot-path contract.
fn classify_and_enqueue(
    tx: &mpsc::Sender<EventRecord>,
    stream: &RequestStream,
    request_id: &RequestId,
    payload: serde_json::Value,
) {
    let Some(kind) = milestone_kind(&payload).map(str::to_owned) else {
        return;
    };
    let record = EventRecord {
        request_id: request_id.clone(),
        seq: stream.next_seq(),
        kind,
        payload,
        created_at: UtcDateTime::now(),
    };
    if tx.try_send(record).is_err() {
        stream.note_drop();
    }
}

/// Milestone classifier: the serde tag of a persist-worthy event, or `None` for a
/// high-volume delta we drop. Fail-safe — an unknown future event kind is treated
/// as a milestone (kept visible) rather than silently dropped.
fn milestone_kind(payload: &serde_json::Value) -> Option<&str> {
    let kind = payload.get("type").and_then(serde_json::Value::as_str)?;
    (!is_delta(kind)).then_some(kind)
}

/// Whether a stream-event kind is a high-volume incremental delta (not persisted).
fn is_delta(kind: &str) -> bool {
    matches!(
        kind,
        "reasoning_delta" | "assistant_text_delta" | "tool_execution_progress"
    )
}

/// Per-request drainer: persist-before-broadcast, coalescing armed drops into one
/// durable gap marker. Exits when the queue closes (the run dropped its callback).
async fn drain(
    mut rx: mpsc::Receiver<EventRecord>,
    stream: Arc<RequestStream>,
    event_log: EventLogRepo,
    request_id: RequestId,
) {
    while let Some(record) = rx.recv().await {
        persist_and_broadcast(&event_log, &stream, record).await;
        if stream.gap_pending.swap(false, Ordering::AcqRel) {
            emit_gap(&event_log, &stream, &request_id).await;
        }
    }
    // A drop armed after the final drained record still owes a marker.
    if stream.gap_pending.swap(false, Ordering::AcqRel) {
        emit_gap(&event_log, &stream, &request_id).await;
    }
}

/// Persist a record, then broadcast it. A record that cannot be durably written is
/// never broadcast (persist-before-broadcast); it is dropped and arms a gap marker.
async fn persist_and_broadcast(event_log: &EventLogRepo, stream: &RequestStream, record: EventRecord) {
    match append_with_retry(event_log, &record).await {
        Ok(()) => {
            // `send` errs only when there are no subscribers — expected and fine.
            let _ = stream.live.send(record);
        }
        Err(err) => {
            tracing::warn!(
                seq = record.seq,
                error = %err,
                "event_log append failed after retry; dropping record and marking a stream gap"
            );
            stream.note_drop();
        }
    }
}

/// Bounded retry for a transient append failure (sqlite busy contention is already
/// absorbed by the pool busy-timeout; this covers a single spurious error).
async fn append_with_retry(event_log: &EventLogRepo, record: &EventRecord) -> Result<(), StoreError> {
    match event_log.append(record).await {
        Ok(()) => Ok(()),
        Err(_) => event_log.append(record).await,
    }
}

/// Persist and broadcast one `event_stream_gap` marker carrying the cumulative
/// dropped count. Best-effort: a failed marker write is logged, not retried into a
/// loop.
async fn emit_gap(event_log: &EventLogRepo, stream: &RequestStream, request_id: &RequestId) {
    let gap = EventRecord {
        request_id: request_id.clone(),
        seq: stream.next_seq(),
        kind: EVENT_STREAM_GAP.to_owned(),
        payload: serde_json::json!({ "dropped": stream.dropped.load(Ordering::Relaxed) }),
        created_at: UtcDateTime::now(),
    };
    match event_log.append(&gap).await {
        Ok(()) => {
            let _ = stream.live.send(gap);
        }
        Err(err) => tracing::warn!(error = %err, "failed to persist event_stream_gap marker"),
    }
}

/// A replay-then-live subscription. [`EventSubscription::recv`] yields records in
/// `seq` order with no gap and no duplicate across the replay/live handoff, and
/// recovers broadcast lag from the durable log.
///
/// The dedup is a single high-water mark (`last_seq`): replay raises it, live
/// delivers only `seq > last_seq`. This is correct only while records are persisted
/// in `seq` order — see the ordering precondition on `RequestStream::next_seq`. It
/// is robust to *broadcast reordering* (a slow subscriber that lags is refilled
/// from the durable log), but not to *persist reordering*; the latter cannot occur
/// under the current single-emitter model.
#[derive(Debug)]
pub struct EventSubscription {
    request_id: RequestId,
    event_log: EventLogRepo,
    replay: VecDeque<EventRecord>,
    /// `None` once the stream is exhausted, or for a replay-only subscription whose
    /// run already finished (no live broadcast to join).
    live: Option<broadcast::Receiver<EventRecord>>,
    /// Highest `seq` delivered so far — the dedup high-water mark.
    last_seq: i64,
}

impl EventSubscription {
    /// The next record in `seq` order, or `None` when the stream is exhausted.
    ///
    /// Drains buffered replay first (skipping any `seq <= last_seq`), then tails the
    /// live broadcast (delivering only `seq > last_seq`). On broadcast lag it
    /// refills from the durable `event_log` instead of skipping; on close it does a
    /// final durable sweep before ending.
    ///
    /// # Errors
    /// [`StoreError`] if a durable refill read fails.
    pub async fn recv(&mut self) -> Result<Option<EventRecord>, StoreError> {
        loop {
            if let Some(record) = self.replay.pop_front() {
                if record.seq > self.last_seq {
                    self.last_seq = record.seq;
                    return Ok(Some(record));
                }
                continue; // already delivered — dedup at the replay/live boundary.
            }
            let Some(live) = self.live.as_mut() else {
                return Ok(None); // replay-only (stream already finished) and drained.
            };
            match live.recv().await {
                Ok(record) => {
                    if record.seq > self.last_seq {
                        self.last_seq = record.seq;
                        return Ok(Some(record));
                    }
                    // A replayed seq echoed live — skip it.
                }
                Err(broadcast::error::RecvError::Lagged(_)) => {
                    // The dropped live records are durable (persist-before-broadcast),
                    // so recover them from the log rather than leaving a gap.
                    let refill = self.event_log.list_since(&self.request_id, self.last_seq).await?;
                    self.replay.extend(refill);
                }
                Err(broadcast::error::RecvError::Closed) => {
                    // Producer gone: one last durable sweep so a record persisted just
                    // before close is never missed, then end the stream.
                    let refill = self.event_log.list_since(&self.request_id, self.last_seq).await?;
                    if refill.is_empty() {
                        self.live = None;
                        return Ok(None);
                    }
                    self.replay.extend(refill);
                }
            }
        }
    }
}

#[cfg(test)]
#[path = "../tests/event_bus/mod.rs"]
mod tests;
