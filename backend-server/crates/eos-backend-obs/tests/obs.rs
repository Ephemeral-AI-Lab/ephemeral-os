//! Phase 6 obs pipeline behaviors, exercised through the public crate surface:
//!
//! - [`PersistingSink`] async-drains agent-core audit events into `obs_event`,
//!   returns backpressure (never blocks) on a full queue, and counts persist
//!   failures (AC6).
//! - [`AuditIngestor`] joins daemon audit to model ids through the correlation
//!   bridge without collapsing `tool_use_id` and `sandbox_invocation_id` (AC7),
//!   and resets the cursor on a daemon reboot (AC8).
//! - [`StatsReader`] surfaces matched/unmatched audit, in-flight + persist loss,
//!   and reboot loss.
#![allow(clippy::unwrap_used)] // unwrap is permitted in tests

use eos_audit::{
    AuditEvent, AuditNode, AuditSink, AuditSource, AGENT_RUN_COMPLETED, OS_RESOURCE_SAMPLED,
    TOOL_CALL_COMPLETED,
};
use eos_backend_obs::{AuditIngestor, PersistingSink, SinkLoss, StatsReader, UNMATCHED_MARKER};
use eos_backend_store::BackendStore;
use eos_backend_types::{ObsSource, Page, SandboxCallCorrelation};
use eos_protocol::audit::SCHEMA_VERSION;
use eos_protocol::CallerId;
use eos_types::{
    AgentRunId, InvocationId, JsonObject, RequestId, SandboxId, TaskId, TestClock, ToolUseId,
    UtcDateTime,
};
use serde_json::{json, Value};
use tempfile::TempDir;

// --- helpers ---

async fn open_store() -> (BackendStore, TempDir) {
    let dir = TempDir::new().unwrap();
    let store = BackendStore::open(dir.path().join("backend.db"))
        .await
        .unwrap();
    (store, dir)
}

fn ingestor(store: &BackendStore) -> AuditIngestor {
    AuditIngestor::new(
        store.obs_events().clone(),
        store.correlations().clone(),
        store.audit_cursors().clone(),
    )
}

fn stats(store: &BackendStore) -> StatsReader {
    StatsReader::new(store.obs_events().clone(), store.audit_cursors().clone())
}

fn clock() -> TestClock {
    TestClock::new(UtcDateTime::parse_rfc3339("2026-06-06T00:00:00Z").unwrap())
}

fn ts(s: &str) -> UtcDateTime {
    UtcDateTime::parse_rfc3339(s).unwrap()
}

fn obj(value: Value) -> JsonObject {
    value.as_object().unwrap().clone()
}

fn rid(s: &str) -> RequestId {
    s.parse().unwrap()
}
fn tid(s: &str) -> TaskId {
    s.parse().unwrap()
}
fn arid(s: &str) -> AgentRunId {
    s.parse().unwrap()
}
fn tuid(s: &str) -> ToolUseId {
    s.parse().unwrap()
}
fn inv(s: &str) -> InvocationId {
    s.parse().unwrap()
}
fn sid(s: &str) -> SandboxId {
    s.parse().unwrap()
}

/// An engine-source audit event carrying the given node and payload.
fn engine_event(kind: &str, node: AuditNode, payload: Value) -> AuditEvent {
    AuditEvent::new(AuditSource::Engine, kind, node, obj(payload), &clock())
}

/// A minimal engine tool-call event tagged by tool-use id (sink queue tests).
fn engine_tool_call(tool_use: &str) -> AuditEvent {
    let node = AuditNode::builder().tool_use_id(tuid(tool_use)).build();
    engine_event(TOOL_CALL_COMPLETED, node, json!({}))
}

/// One daemon tool-call audit event. The daemon stamps its own invocation id into
/// the `tool_call.tool_use_id` slot (mirrors the daemon transport server).
fn daemon_tool_call(seq: i64, invocation: &str, caller: &str, duration_ms: f64) -> Value {
    json!({
        "type": TOOL_CALL_COMPLETED,
        "seq": seq,
        "lane": "normal",
        "payload": {
            "tool_call": {
                "tool_use_id": invocation,
                "tool_name": "exec_command",
                "caller_id": caller,
                "duration_ms": duration_ms,
                "status": "ok"
            }
        }
    })
}

/// A daemon `api.audit.pull` response wrapping `events`.
fn daemon_pull(
    boot_epoch_id: i64,
    events: Vec<Value>,
    cursor_after: i64,
    lost_before: Option<i64>,
    dropped: i64,
) -> Value {
    json!({
        "schema": SCHEMA_VERSION,
        "cursor": { "after_seq": cursor_after, "lost_before_seq": lost_before },
        "buffer": { "dropped_event_count": dropped, "lost_before_seq": lost_before },
        "snapshot": { "daemon": { "boot_epoch_id": boot_epoch_id, "next_seq": cursor_after + 1 } },
        "events": events,
    })
}

// --- sink ---

#[tokio::test]
async fn sink_async_drains_engine_events_with_distinct_ids() {
    let (store, _dir) = open_store().await;
    let (sink, shutdown) = PersistingSink::new(store.obs_events().clone());

    let node = AuditNode::builder()
        .request_id(rid("r-1"))
        .task_id(tid("t-1"))
        .agent_run_id(arid("ar-1"))
        .tool_use_id(tuid("toolu_model"))
        .sandbox_id(sid("sb-1"))
        .tool_name("read_file")
        .build();
    sink.publish(&engine_event(
        TOOL_CALL_COMPLETED,
        node,
        json!({ "tool_call": { "duration_ms": 3.0, "status": "ok" } }),
    ))
    .unwrap();
    // The drainer (not `publish`) performs the async SQLite write; await it.
    shutdown.shutdown().await;

    let rows = store.obs_events().list_for_request(&rid("r-1")).await.unwrap();
    assert_eq!(rows.len(), 1);
    let row = &rows[0];
    assert_eq!(row.source, ObsSource::Engine);
    assert_eq!(row.kind, TOOL_CALL_COMPLETED);
    // Model id from the node; the engine path carries no daemon invocation id.
    assert_eq!(row.tool_use_id.as_ref().map(ToolUseId::as_str), Some("toolu_model"));
    assert!(row.sandbox_invocation_id.is_none());
    assert_eq!(row.sandbox_id.as_ref().map(SandboxId::as_str), Some("sb-1"));
    // The node's tool_name was folded into the payload.
    assert_eq!(row.payload.get("tool_name"), Some(&json!("read_file")));
}

#[tokio::test]
async fn sink_publish_overflow_returns_backpressure_and_counts_drops() {
    let (store, _dir) = open_store().await;
    let (sink, _shutdown) = PersistingSink::with_capacity(store.obs_events().clone(), 2);

    // On the default current-thread test runtime the drainer task is not polled
    // while this synchronous loop runs (no `.await`), so the bounded queue fills
    // deterministically: 2 accepted, the next 3 rejected with backpressure.
    let mut backpressure = 0u64;
    for i in 0..5 {
        if sink.publish(&engine_tool_call(&format!("toolu-{i}"))).is_err() {
            backpressure += 1;
        }
    }
    assert_eq!(backpressure, 3);
    assert_eq!(sink.loss_snapshot().dropped_inflight, 3);

    // Stats surface the in-flight drop count (queue overflow).
    let loss = stats(&store).obs_loss(sink.loss_snapshot()).await.unwrap();
    assert_eq!(loss.obs_dropped_inflight, 3);
}

#[tokio::test]
async fn sink_drainer_failure_counts_persist_loss() {
    let (store, _dir) = open_store().await;
    let (sink, shutdown) = PersistingSink::with_capacity(store.obs_events().clone(), 8);

    // Force every insert to fail deterministically: drop the destination table via
    // the store's public pool. audit_cursor survives, so stats still read.
    sqlx::query("DROP TABLE obs_event")
        .execute(store.pool())
        .await
        .unwrap();
    sink.publish(&engine_tool_call("toolu-1")).unwrap();
    // Drain: the insert fails (with one retry) and counts a persist loss.
    shutdown.shutdown().await;

    assert_eq!(sink.loss_snapshot().persist_failed, 1);
    let loss = stats(&store).obs_loss(sink.loss_snapshot()).await.unwrap();
    assert_eq!(loss.obs_persist_failed, 1);
}

// --- ingestor ---

#[tokio::test]
async fn ingest_matches_daemon_event_through_bridge_without_id_collapse() {
    let (store, _dir) = open_store().await;
    let sandbox = sid("sb-1");

    // The correlation bridge is recorded before the (simulated) daemon dispatch.
    store
        .correlations()
        .insert(&SandboxCallCorrelation {
            request_id: rid("r-1"),
            task_id: tid("t-1"),
            agent_run_id: arid("ar-1"),
            tool_use_id: tuid("toolu_model"),
            sandbox_invocation_id: inv("inv-1"),
            caller_id: CallerId("caller-9".into()),
            sandbox_id: sandbox.clone(),
            created_at: ts("2026-06-06T00:00:00Z"),
        })
        .await
        .unwrap();

    let pull = daemon_pull(
        1,
        vec![daemon_tool_call(1, "inv-1", "caller-9", 5.0)],
        1,
        None,
        0,
    );
    let report = ingestor(&store).ingest_pull(&sandbox, &pull).await.unwrap();
    assert_eq!((report.matched, report.unmatched), (1, 0));

    let rows = store.obs_events().list_for_request(&rid("r-1")).await.unwrap();
    assert_eq!(rows.len(), 1);
    let row = &rows[0];
    assert_eq!(row.source, ObsSource::Daemon);
    // Model id comes ONLY from the bridge; the daemon invocation stays in the
    // invocation slot; the two are never reused as one another (AC7).
    assert_eq!(row.tool_use_id.as_ref().map(ToolUseId::as_str), Some("toolu_model"));
    assert_eq!(
        row.sandbox_invocation_id.as_ref().map(InvocationId::as_str),
        Some("inv-1")
    );
    assert_ne!(
        row.tool_use_id.as_ref().map(ToolUseId::as_str),
        row.sandbox_invocation_id.as_ref().map(InvocationId::as_str)
    );

    assert_eq!(stats(&store).correctness().await.unwrap().audit_matched, 1);
}

#[tokio::test]
async fn ingest_unmatched_daemon_event_persists_null_model_ids_and_marker() {
    let (store, _dir) = open_store().await;
    let sandbox = sid("sb-1");

    // No bridge row exists for this invocation.
    let pull = daemon_pull(
        1,
        vec![daemon_tool_call(1, "inv-unknown", "caller-x", 5.0)],
        1,
        None,
        0,
    );
    let report = ingestor(&store).ingest_pull(&sandbox, &pull).await.unwrap();
    assert_eq!((report.matched, report.unmatched), (0, 1));

    let page = store.obs_events().list_page(Page::default()).await.unwrap();
    assert_eq!(page.items.len(), 1);
    let row = &page.items[0];
    assert!(row.request_id.is_none());
    assert!(row.task_id.is_none());
    assert!(row.agent_run_id.is_none());
    // The invocation id is NEVER copied into tool_use_id.
    assert!(row.tool_use_id.is_none());
    assert_eq!(
        row.sandbox_invocation_id.as_ref().map(InvocationId::as_str),
        Some("inv-unknown")
    );
    assert_eq!(row.payload.get(UNMATCHED_MARKER), Some(&json!(true)));

    assert_eq!(stats(&store).correctness().await.unwrap().audit_unmatched, 1);
}

#[tokio::test]
async fn ingest_daemon_reboot_records_loss_and_resets_cursor() {
    let (store, _dir) = open_store().await;
    let sandbox = sid("sb-1");
    let ingestor = ingestor(&store);

    // Epoch 1: consume two events; cursor advances to seq 2 with no loss.
    let pull1 = daemon_pull(
        1,
        vec![
            daemon_tool_call(1, "inv-a", "c", 1.0),
            daemon_tool_call(2, "inv-b", "c", 1.0),
        ],
        2,
        None,
        0,
    );
    let first = ingestor.ingest_pull(&sandbox, &pull1).await.unwrap();
    assert!(!first.epoch_reset);
    assert_eq!(first.cursor.boot_epoch_id, 1);
    assert_eq!(first.cursor.last_seq, 2);
    assert!(first.cursor.lost_before_seq.is_none());

    // Epoch 2 (daemon reboot): a fresh sequence space plus reported drops. The
    // cursor records loss for the prior epoch BEFORE resetting last_seq (AC8).
    let pull2 = daemon_pull(2, vec![daemon_tool_call(1, "inv-c", "c", 1.0)], 1, None, 4);
    let second = ingestor.ingest_pull(&sandbox, &pull2).await.unwrap();
    assert!(second.epoch_reset);
    assert_eq!(second.cursor.boot_epoch_id, 2);
    assert_eq!(second.cursor.last_seq, 1);
    assert_eq!(second.cursor.lost_before_seq, Some(2));
    assert_eq!(second.cursor.dropped_count, 4);

    // The persisted cursor carries the reset epoch/sequence and recorded loss.
    let stored = store.audit_cursors().get(&sandbox).await.unwrap().unwrap();
    assert_eq!(stored.boot_epoch_id, 2);
    assert_eq!(stored.last_seq, 1);
    assert_eq!(stored.lost_before_seq, Some(2));
    assert_eq!(stored.dropped_count, 4);

    // Stats loss accounting reflects the reboot.
    let loss = stats(&store).obs_loss(SinkLoss::default()).await.unwrap();
    assert_eq!(loss.audit_sandboxes_with_loss, 1);
    assert_eq!(loss.audit_dropped, 4);
}

// --- stats over engine obs rows ---

#[tokio::test]
async fn stats_performance_and_agent_runs_from_obs_rows() {
    let (store, _dir) = open_store().await;
    let (sink, shutdown) = PersistingSink::new(store.obs_events().clone());

    let run = |kind: &str, payload: Value| {
        engine_event(kind, AuditNode::builder().agent_run_id(arid("ar-1")).build(), payload)
    };
    sink.publish(&run(TOOL_CALL_COMPLETED, json!({ "tool_call": { "duration_ms": 10.0 } })))
        .unwrap();
    sink.publish(&run(TOOL_CALL_COMPLETED, json!({ "tool_call": { "duration_ms": 30.0 } })))
        .unwrap();
    sink.publish(&run(OS_RESOURCE_SAMPLED, json!({ "os_resource": { "rss_bytes": 2048 } })))
        .unwrap();
    sink.publish(&run(AGENT_RUN_COMPLETED, json!({}))).unwrap();
    shutdown.shutdown().await;

    let stats = stats(&store);

    let perf = stats.performance().await.unwrap();
    assert_eq!(perf.tool_call_count, 2);
    assert_eq!(perf.tool_call_total_ms, 40.0);
    assert_eq!(perf.tool_call_avg_ms, Some(20.0));
    assert_eq!(perf.resource_sample_count, 1);
    assert_eq!(perf.rss_bytes_max, Some(2048));

    let correctness = stats.correctness().await.unwrap();
    assert_eq!(correctness.agent_runs_observed, 1);
    assert_eq!(correctness.tool_calls_observed, 2);
    assert_eq!((correctness.audit_matched, correctness.audit_unmatched), (0, 0));

    let runs = stats.agent_runs().await.unwrap();
    assert_eq!(runs.len(), 1);
    assert_eq!(runs[0].agent_run_id.as_str(), "ar-1");
    assert_eq!(runs[0].tool_call_count, 2);
    assert_eq!(runs[0].tool_call_total_ms, 40.0);
    assert_eq!(runs[0].resource_sample_count, 1);

    let page = stats.events(Page::default()).await.unwrap();
    assert_eq!(page.total, 4);
    assert_eq!(page.items.len(), 4);
}
