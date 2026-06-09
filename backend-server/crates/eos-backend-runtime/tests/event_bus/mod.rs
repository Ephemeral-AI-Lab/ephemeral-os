//! [`EventBus`] tests. Included into `crate::event_bus` under `#[cfg(test)]`, so it
//! reaches the private `open_stream`/`classify_and_enqueue`/`milestone_kind` and
//! the `RequestStream`/`EventSubscription` fields. This matters because the engine
//! `StreamEvent` is `#[non_exhaustive]` and cannot be built here — the tests drive
//! the exact post-serialize enqueue path the production callback runs, with
//! pre-serialized payloads.
#![allow(clippy::unwrap_used)]

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, AtomicU64};
use std::sync::Arc;
use std::time::Duration;

use tokio::sync::{broadcast, mpsc};

use eos_backend_types::{EventRecord, EVENT_STREAM_GAP};
use eos_types::{RequestId, UtcDateTime};

use crate::test_support::{rid, temp_store};

use super::{
    classify_and_enqueue, drain, milestone_kind, EventBus, EventSubscription, PendingMilestone,
    RequestStream,
};

fn milestone(seq: i64, request: &RequestId, kind: &str) -> EventRecord {
    EventRecord {
        request_id: request.clone(),
        seq,
        kind: kind.to_owned(),
        payload: serde_json::json!({ "type": kind, "seq": seq }),
        created_at: UtcDateTime::now(),
    }
}

async fn await_persisted(
    store: &eos_backend_store::BackendStore,
    request: &RequestId,
    target: i64,
) {
    for _ in 0..500 {
        if store
            .event_log()
            .max_seq(request)
            .await
            .unwrap()
            .unwrap_or(0)
            >= target
        {
            return;
        }
        tokio::time::sleep(Duration::from_millis(2)).await;
    }
    panic!("event_log did not reach seq {target} in time");
}

// --- classification + seq ----------------------------------------------------

#[test]
fn milestone_kind_drops_deltas_keeps_milestones_and_unknown() {
    for delta in [
        "reasoning_delta",
        "assistant_text_delta",
        "tool_execution_progress",
    ] {
        assert_eq!(milestone_kind(&serde_json::json!({ "type": delta })), None);
    }
    for kind in [
        "assistant_message_complete",
        "tool_execution_started",
        "tool_execution_completed",
        "tool_execution_cancelled",
        "system_notification",
    ] {
        assert_eq!(
            milestone_kind(&serde_json::json!({ "type": kind })),
            Some(kind)
        );
    }
    // Fail-safe: an unknown future kind is kept visible, never silently dropped.
    assert_eq!(
        milestone_kind(&serde_json::json!({ "type": "future_event" })),
        Some("future_event")
    );
    // No discriminant ⇒ unclassifiable, skipped.
    assert_eq!(milestone_kind(&serde_json::json!({ "nope": 1 })), None);
}

#[tokio::test]
async fn drainer_sequences_in_persist_order_so_a_gap_never_leapfrogs() {
    // Regression for the gap-marker leapfrog. The single drainer stamps `seq` as it
    // persists, so a gap armed mid-backlog is sequenced *in place* — never above the
    // records still queued behind it. Feed three milestones behind an already-armed
    // overflow drop and assert the durable log and the live tail both come out in
    // contiguous, strictly increasing `seq` order with the gap interleaved, not
    // jumped ahead. Under the old lazy-`next_seq` design the gap took a seq above all
    // three, and the live high-water dedup then skipped seq 3/4.
    let (store, _tmp) = temp_store().await;
    let event_log = store.event_log().clone();
    let request = rid("req-order");

    // An overflow already dropped 2 records before draining starts.
    let (live, mut live_rx) = broadcast::channel::<EventRecord>(64);
    let stream = Arc::new(RequestStream {
        gap_pending: AtomicBool::new(true),
        dropped: AtomicU64::new(2),
        live,
    });

    let (tx, rx) = mpsc::channel::<PendingMilestone>(8);
    for kind in [
        "tool_execution_started",
        "tool_execution_completed",
        "system_notification",
    ] {
        tx.try_send(PendingMilestone {
            kind: kind.to_owned(),
            payload: serde_json::json!({ "type": kind }),
            created_at: UtcDateTime::now(),
        })
        .unwrap();
    }
    drop(tx); // close the queue so `drain` returns once it has drained.

    drain(rx, stream, event_log.clone(), request.clone()).await;

    // Durable log: 3 reals + 1 gap, contiguous 1..=4, ascending, gap sequenced in
    // place (right after the first record, where the drop was armed).
    let rows = event_log.list_since(&request, 0).await.unwrap();
    let seqs: Vec<i64> = rows.iter().map(|r| r.seq).collect();
    assert_eq!(seqs, vec![1, 2, 3, 4], "seqs are hole-free and ascending");
    let gap_seqs: Vec<i64> = rows
        .iter()
        .filter(|r| r.kind == EVENT_STREAM_GAP)
        .map(|r| r.seq)
        .collect();
    assert_eq!(
        gap_seqs,
        vec![2],
        "one gap, sequenced in place — not leapfrogged"
    );
    let gap = rows.iter().find(|r| r.kind == EVENT_STREAM_GAP).unwrap();
    assert_eq!(
        gap.payload
            .get("dropped")
            .and_then(serde_json::Value::as_u64),
        Some(2)
    );

    // Live tail: same order, strictly increasing — the gap never jumps ahead of a
    // record the dedup has not yet delivered.
    let mut live_seqs = Vec::new();
    while let Ok(record) = live_rx.try_recv() {
        live_seqs.push(record.seq);
    }
    assert_eq!(
        live_seqs,
        vec![1, 2, 3, 4],
        "live broadcast matches seq order"
    );
}

// --- drainer + handoff -------------------------------------------------------

#[tokio::test]
async fn drainer_persists_before_broadcasting() {
    let (store, _tmp) = temp_store().await;
    let bus = EventBus::with_capacity(store.event_log().clone(), 64, 64);
    let request = rid("req-pbb");
    let (tx, stream) = bus.open_stream(&request);

    // Subscribe to live before any event exists (replay empty).
    let mut sub = bus.subscribe(&request, 0).await.unwrap();
    classify_and_enqueue(
        &tx,
        &stream,
        serde_json::json!({ "type": "assistant_message_complete" }),
    );

    // Receiving the broadcast implies it was persisted first (persist-before-
    // broadcast), so the durable row is already present.
    let record = sub.recv().await.unwrap().unwrap();
    assert_eq!(record.seq, 1);
    let rows = store.event_log().list_since(&request, 0).await.unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].seq, 1);
}

#[tokio::test]
async fn reconnect_replays_then_joins_live_with_no_gap() {
    let (store, _tmp) = temp_store().await;
    let bus = EventBus::with_capacity(store.event_log().clone(), 64, 64);
    let request = rid("req-reconnect");
    let (tx, stream) = bus.open_stream(&request);

    // Two milestones persisted before the client connects.
    classify_and_enqueue(
        &tx,
        &stream,
        serde_json::json!({ "type": "tool_execution_started" }),
    );
    classify_and_enqueue(
        &tx,
        &stream,
        serde_json::json!({ "type": "tool_execution_completed" }),
    );
    await_persisted(&store, &request, 2).await;

    // Connect: replay 1,2 then join live.
    let mut sub = bus.subscribe(&request, 0).await.unwrap();
    // A third milestone arrives after the replay snapshot — it must not fall in the
    // handoff.
    classify_and_enqueue(
        &tx,
        &stream,
        serde_json::json!({ "type": "system_notification" }),
    );

    let mut seen = Vec::new();
    for _ in 0..3 {
        seen.push(sub.recv().await.unwrap().unwrap().seq);
    }
    assert_eq!(seen, vec![1, 2, 3], "replay/live handoff must be gapless");
}

#[tokio::test]
async fn subscription_recovers_broadcast_lag_from_the_durable_log() {
    // The advisor-flagged case: a slow subscriber lags the broadcast. The dropped
    // live records are durable (persist-before-broadcast), so recv must recover
    // them from the log rather than skip past them.
    let (store, _tmp) = temp_store().await;
    let event_log = store.event_log().clone();
    let request = rid("req-lag");
    for seq in 1..=5 {
        event_log
            .append(&milestone(seq, &request, "tool_execution_completed"))
            .await
            .unwrap();
    }

    // Broadcast capacity 2, then push 5 with no receiver draining ⇒ the receiver
    // lags. Drop the sender so the stream terminates after the durable sweep.
    let (live, rx) = broadcast::channel::<EventRecord>(2);
    for seq in 1..=5 {
        let _ = live.send(milestone(seq, &request, "tool_execution_completed"));
    }
    drop(live);

    let mut sub = EventSubscription {
        request_id: request.clone(),
        event_log,
        replay: VecDeque::new(),
        live: Some(rx),
        last_seq: 0,
    };
    let mut seen = Vec::new();
    while let Some(record) = sub.recv().await.unwrap() {
        seen.push(record.seq);
    }
    assert_eq!(
        seen,
        vec![1, 2, 3, 4, 5],
        "lag must be recovered, not skipped"
    );
}

// --- bounded overflow --------------------------------------------------------

#[tokio::test]
async fn bounded_queue_overflow_drops_and_emits_a_visible_gap_marker() {
    let (store, _tmp) = temp_store().await;
    // Queue capacity 1: on a current-thread runtime the spawned drainer cannot run
    // until we await, so the synchronous burst fills the queue deterministically.
    let bus = EventBus::with_capacity(store.event_log().clone(), 1, 64);
    let request = rid("req-overflow");
    let (tx, stream) = bus.open_stream(&request);

    for i in 0..5 {
        classify_and_enqueue(
            &tx,
            &stream,
            serde_json::json!({ "type": "tool_execution_completed", "i": i }),
        );
    }
    // One buffered, four dropped — asserted below via the gap marker's count.
    // Close the queue and let the drainer flush the buffered record + the gap.
    drop(tx);
    let mut rows = Vec::new();
    for _ in 0..500 {
        rows = store.event_log().list_since(&request, 0).await.unwrap();
        if rows.iter().any(|r| r.kind == EVENT_STREAM_GAP) {
            break;
        }
        tokio::time::sleep(Duration::from_millis(2)).await;
    }

    let gaps = rows.iter().filter(|r| r.kind == EVENT_STREAM_GAP).count();
    let reals = rows.iter().filter(|r| r.kind != EVENT_STREAM_GAP).count();
    assert_eq!(reals, 1, "exactly the one buffered milestone persisted");
    assert_eq!(gaps, 1, "loss surfaces as exactly one gap marker");
    let gap = rows.iter().find(|r| r.kind == EVENT_STREAM_GAP).unwrap();
    assert_eq!(
        gap.payload
            .get("dropped")
            .and_then(serde_json::Value::as_u64),
        Some(4)
    );
}
