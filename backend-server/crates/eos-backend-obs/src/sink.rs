//! [`PersistingSink`] — an [`AuditSink`] that persists agent-core audit events to
//! `obs_event` without blocking the engine hot path (AC6).
//!
//! [`AuditSink::publish`] is synchronous and borrows its [`AuditEvent`]. This sink
//! converts the borrowed event into an **owned** [`ObsEvent`] and `try_send`s it
//! into a bounded channel; a full queue returns [`AuditError::Backpressure`] and
//! bumps a dropped counter — `publish` never `.await`s and never touches `SQLite`. A
//! dedicated async drainer owns each payload and persists it through
//! [`ObsEventRepo`], retrying once on a transient failure and otherwise counting a
//! durable persist loss. The flow mirrors `eos_audit::BufferedJsonlSink` (a bounded
//! queue + drainer + an explicit shutdown that flushes accepted events), but the
//! drainer is a Tokio task because the destination write is async.
//!
//! Source mapping: every event published through this sink is agent-core
//! engine/tool-path observability, so its persisted [`ObsSource`] is `Engine`.
//! Daemon-pulled rows are `Daemon` and flow through the [`ingestor`](crate::ingestor)
//! instead. The [`AuditNode`](eos_audit::AuditNode) carries no daemon invocation id,
//! so the engine-path `sandbox_invocation_id` is always null here — the model-facing
//! `tool_use_id`/`sandbox_id` come straight from the node.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use serde_json::Value;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

use eos_audit::{AuditError, AuditEvent, AuditSink};
use eos_backend_store::{ObsEventRepo, StoreError};
use eos_backend_types::{ObsEvent, ObsSource};

/// Default bound on the `publish`→drainer queue (events in flight before overflow
/// returns [`AuditError::Backpressure`]).
const DEFAULT_QUEUE_CAPACITY: usize = 1024;

/// Control message from a synchronous `publish` (or the shutdown guard) to the
/// async drainer. The event is boxed so the enum stays small.
enum Msg {
    /// One owned event to persist.
    Event(Box<ObsEvent>),
    /// Flush every event already queued, then stop the drainer.
    Shutdown,
}

/// In-memory loss counters shared by the synchronous `publish` and the async
/// drainer; read back through [`PersistingSink::loss_snapshot`].
#[derive(Debug, Default)]
struct SinkCounters {
    /// Events dropped at `publish` because the bounded queue was full or closed.
    dropped: AtomicU64,
    /// Accepted events the drainer could not durably persist after one retry.
    persist_failed: AtomicU64,
}

/// A point-in-time read of a [`PersistingSink`]'s in-memory loss counters.
///
/// These are not durable (the failing resource is the database itself), so they
/// live in memory and are surfaced to `/api/stats/*` by the stats reader rather
/// than written back to `backend.db`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct SinkLoss {
    /// Events dropped at `publish` because the bounded queue was full.
    pub dropped_inflight: u64,
    /// Accepted events the drainer could not durably persist after retry.
    pub persist_failed: u64,
}

/// Backend [`AuditSink`] that async-drains agent-core audit events into `obs_event`.
///
/// Construct with [`PersistingSink::new`], wrap in `Arc<dyn AuditSink>`, and inject
/// at the agent-core composition root. Retain the returned [`PersistingSinkShutdown`]
/// to flush accepted events on teardown.
#[derive(Debug, Clone)]
pub struct PersistingSink {
    tx: mpsc::Sender<Msg>,
    counters: Arc<SinkCounters>,
}

impl PersistingSink {
    /// Build a sink draining into `obs_events` with the default queue capacity.
    /// Spawns the drainer; must be called within a Tokio runtime.
    #[must_use]
    pub fn new(obs_events: ObsEventRepo) -> (Self, PersistingSinkShutdown) {
        Self::with_capacity(obs_events, DEFAULT_QUEUE_CAPACITY)
    }

    /// Build a sink with an explicit queue bound (used by tests to force overflow
    /// deterministically). The bound is floored at 1.
    #[must_use]
    pub fn with_capacity(
        obs_events: ObsEventRepo,
        capacity: usize,
    ) -> (Self, PersistingSinkShutdown) {
        let (tx, rx) = mpsc::channel::<Msg>(capacity.max(1));
        let counters = Arc::new(SinkCounters::default());
        let handle = tokio::spawn(drain(rx, obs_events, counters.clone()));
        (
            Self {
                tx: tx.clone(),
                counters,
            },
            PersistingSinkShutdown {
                ctrl_tx: Some(tx),
                handle: Some(handle),
            },
        )
    }

    /// A snapshot of the in-memory loss counters for `/api/stats/*` loss accounting.
    #[must_use]
    pub fn loss_snapshot(&self) -> SinkLoss {
        SinkLoss {
            dropped_inflight: self.counters.dropped.load(Ordering::Relaxed),
            persist_failed: self.counters.persist_failed.load(Ordering::Relaxed),
        }
    }
}

impl AuditSink for PersistingSink {
    fn publish(&self, event: &AuditEvent) -> Result<(), AuditError> {
        // Build an OWNED row from the borrowed event (the queue never holds a
        // reference into `event`), then enqueue without awaiting.
        let owned = to_obs_event(event);
        match self.tx.try_send(Msg::Event(Box::new(owned))) {
            Ok(()) => Ok(()),
            // Full queue or a gone drainer: drop, count it, and signal backpressure.
            // The engine may log this but must not block on SQLite.
            Err(mpsc::error::TrySendError::Full(_) | mpsc::error::TrySendError::Closed(_)) => {
                self.counters.dropped.fetch_add(1, Ordering::Relaxed);
                Err(AuditError::Backpressure)
            }
        }
    }
}

/// Convert a borrowed agent-core [`AuditEvent`] into an owned engine-source
/// [`ObsEvent`]. Model-facing ids come from the node; `sandbox_invocation_id` is
/// always null on this path (the node has no daemon invocation id). `agent_name`
/// and `tool_name` are folded into the payload for downstream labels, mirroring
/// `AuditEvent::to_obs_envelope`.
fn to_obs_event(event: &AuditEvent) -> ObsEvent {
    let node = &event.node;
    let mut payload = event.payload.clone();
    if let Some(agent_name) = &node.agent_name {
        payload
            .entry("agent_name".to_owned())
            .or_insert_with(|| Value::String(agent_name.clone()));
    }
    if let Some(tool_name) = &node.tool_name {
        payload
            .entry("tool_name".to_owned())
            .or_insert_with(|| Value::String(tool_name.clone()));
    }
    ObsEvent {
        id: None,
        request_id: node.request_id.clone(),
        task_id: node.task_id.clone(),
        agent_run_id: node.agent_run_id.clone(),
        tool_use_id: node.tool_use_id.clone(),
        sandbox_invocation_id: None,
        sandbox_id: node.sandbox_id.clone(),
        source: ObsSource::Engine,
        kind: event.event_type.clone(),
        payload: Value::Object(payload),
        created_at: event.ts,
    }
}

/// Drain accepted events to `obs_event` in FIFO order. Exits on [`Msg::Shutdown`]
/// (every event queued before it is persisted first) or when every sender drops.
async fn drain(mut rx: mpsc::Receiver<Msg>, obs_events: ObsEventRepo, counters: Arc<SinkCounters>) {
    while let Some(msg) = rx.recv().await {
        match msg {
            Msg::Event(event) => persist(&obs_events, &counters, &event).await,
            Msg::Shutdown => break,
        }
    }
}

/// Persist one event, retrying once on a transient failure. A persistent failure
/// is counted (the database is the failing resource, so the honest fallback is an
/// in-memory loss counter plus a warning, not a database write).
async fn persist(obs_events: &ObsEventRepo, counters: &SinkCounters, event: &ObsEvent) {
    if let Err(err) = append_with_retry(obs_events, event).await {
        counters.persist_failed.fetch_add(1, Ordering::Relaxed);
        tracing::warn!(
            kind = %event.kind,
            error = %err,
            "obs_event insert failed after retry; dropping audit event and counting persist loss"
        );
    }
}

/// One retry for a transient insert failure (sqlite busy contention is already
/// absorbed by the pool busy-timeout; this covers a single spurious error).
async fn append_with_retry(obs_events: &ObsEventRepo, event: &ObsEvent) -> Result<i64, StoreError> {
    match obs_events.insert(event).await {
        Ok(id) => Ok(id),
        Err(_) => obs_events.insert(event).await,
    }
}

/// Shutdown guard for a [`PersistingSink`]'s drainer task.
///
/// Retained by the composition root. On [`shutdown`](Self::shutdown) or `Drop` it
/// sends [`Msg::Shutdown`], flushing every event accepted before it, then awaits
/// the drainer. Bound the wait with `tokio::time::timeout` to honor a backend
/// shutdown deadline.
#[derive(Debug)]
pub struct PersistingSinkShutdown {
    ctrl_tx: Option<mpsc::Sender<Msg>>,
    handle: Option<JoinHandle<()>>,
}

impl PersistingSinkShutdown {
    /// Flush accepted events and await the drainer.
    pub async fn shutdown(mut self) {
        // Best-effort flush signal: if the queue is momentarily full the send waits
        // for the drainer to free a slot (it keeps draining), mirroring
        // `BufferedAuditShutdown`. A closed channel means the drainer already exited.
        if let Some(ctrl_tx) = self.ctrl_tx.take() {
            let _ = ctrl_tx.send(Msg::Shutdown).await;
        }
        if let Some(handle) = self.handle.take() {
            let _ = handle.await;
        }
    }
}

// No explicit `Drop`: a bare drop detaches the drainer (dropping the `JoinHandle`
// never aborts it) and drops this control sender, so the task keeps draining
// already-accepted events until every sender is gone, then exits. Callers wanting a
// bounded flush call `shutdown().await`.
