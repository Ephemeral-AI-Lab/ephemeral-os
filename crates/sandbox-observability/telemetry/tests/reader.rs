//! Reader: historical views fold one sorted `scan()` over primary + rotated;
//! latest samples use a bounded streaming pass; `trace` resolves out-of-order
//! records; `samples` Δs only emitter-tagged counters.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use sandbox_observability_telemetry::{RawFilter, Reader};
use serde_json::{json, Value};

static NEXT: AtomicU64 = AtomicU64::new(0);

fn temp_dir(label: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "sandbox-obs-reader-{label}-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir_all(&dir).expect("create temp dir");
    dir
}

fn write_lines(path: &Path, lines: &[Value]) {
    let body: String = lines.iter().map(|line| format!("{line}\n")).collect();
    fs::write(path, body).expect("write log");
}

fn now_ms() -> i64 {
    i64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_millis(),
    )
    .unwrap_or(i64::MAX)
}

#[test]
fn scan_spans_primary_and_rotated_sorted_by_ts() {
    let dir = temp_dir("rotation");
    let primary = dir.join("observability.ndjson");
    let rotated = dir.join("observability.ndjson.1");
    // Rotated (older) and primary (newer) each hold out-of-order lines.
    write_lines(
        &rotated,
        &[
            json!({ "kind": "sample", "ts": 300, "scope": "sandbox" }),
            json!({ "kind": "sample", "ts": 100, "scope": "sandbox" }),
        ],
    );
    write_lines(
        &primary,
        &[
            json!({ "kind": "sample", "ts": 400, "scope": "sandbox" }),
            json!({ "kind": "sample", "ts": 200, "scope": "sandbox" }),
        ],
    );

    let reader = Reader::new(primary, rotated);
    let raw = reader.raw(RawFilter::default());
    let order: Vec<i64> = raw
        .iter()
        .map(|line| {
            serde_json::from_str::<Value>(line).expect("parse")["ts"]
                .as_i64()
                .expect("ts")
        })
        .collect();
    assert_eq!(
        order,
        vec![100, 200, 300, 400],
        "sorted by ts across both files"
    );
}

#[test]
fn scan_skips_malformed_lines() {
    let dir = temp_dir("malformed");
    let primary = dir.join("observability.ndjson");
    let rotated = dir.join("observability.ndjson.1");
    fs::write(
        &primary,
        "{\"kind\":\"sample\",\"ts\":10,\"scope\":\"sandbox\"}\n{\"kind\":\"sample\",\"ts\":20,\"sco",
    )
    .expect("write");

    let reader = Reader::new(primary, rotated);
    assert_eq!(
        reader.raw(RawFilter::default()).len(),
        1,
        "half-written tail skipped"
    );
}

#[test]
fn samples_delta_only_emitter_tagged_counters() {
    let dir = temp_dir("samples");
    let primary = dir.join("observability.ndjson");
    let rotated = dir.join("observability.ndjson.1");
    let base = now_ms() - 2_000;
    write_lines(
        &primary,
        &[
            json!({ "kind": "sample", "ts": base, "scope": "sandbox", "cpu_usec": 100, "mem_cur": 10, "_counters": ["cpu_usec"] }),
            json!({ "kind": "sample", "ts": base + 1_000, "scope": "sandbox", "cpu_usec": 250, "mem_cur": 8, "_counters": ["cpu_usec"] }),
        ],
    );

    let reader = Reader::new(primary, rotated);
    let series = reader.samples("sandbox", 600_000);
    assert_eq!(series.len(), 2);
    assert!(
        series[0].deltas.is_empty(),
        "first in-window sample has no delta"
    );
    assert_eq!(series[1].deltas["cpu_usec"], 150, "counter Δ");
    assert!(!series[1].deltas.contains_key("mem_cur"), "gauge gets no Δ");
    assert_eq!(series[1].sample_delta_ms, Some(1_000));
    assert!(
        !series[1].metrics.contains_key("_counters"),
        "reserved meta key stripped from presented metrics"
    );
    assert_eq!(series[1].metrics["mem_cur"], 8);
}

#[test]
fn samples_filter_by_window() {
    let dir = temp_dir("window");
    let primary = dir.join("observability.ndjson");
    let rotated = dir.join("observability.ndjson.1");
    let now = now_ms();
    write_lines(
        &primary,
        &[
            json!({ "kind": "sample", "ts": now - 500_000, "scope": "sandbox", "cpu_usec": 1 }),
            json!({ "kind": "sample", "ts": now - 1_000, "scope": "sandbox", "cpu_usec": 2 }),
        ],
    );
    let reader = Reader::new(primary, rotated);
    let recent = reader.samples("sandbox", 60_000);
    assert_eq!(recent.len(), 1, "only the in-window sample");
    assert_eq!(recent[0].metrics["cpu_usec"], 2);
}

#[test]
fn latest_samples_keeps_only_the_newest_pair_per_requested_scope() {
    let dir = temp_dir("latest-samples");
    let primary = dir.join("observability.ndjson");
    let rotated = dir.join("observability.ndjson.1");
    write_lines(
        &rotated,
        &[
            json!({ "kind": "sample", "ts": 100, "scope": "sandbox", "cpu_usec": 100, "_counters": ["cpu_usec"] }),
            json!({ "kind": "sample", "ts": 150, "scope": "workspace-1", "disk_bytes": 1 }),
            json!({ "kind": "sample", "ts": 500, "scope": "ignored", "cpu_usec": 9_999 }),
        ],
    );
    write_lines(
        &primary,
        &[
            json!({ "kind": "sample", "ts": 300, "scope": "sandbox", "cpu_usec": 250, "_counters": ["cpu_usec"] }),
            json!({ "kind": "sample", "ts": 200, "scope": "sandbox", "cpu_usec": 150, "_counters": ["cpu_usec"] }),
            json!({ "kind": "sample", "ts": 250, "scope": "workspace-1", "disk_bytes": 3 }),
        ],
    );

    let reader = Reader::new(primary, rotated);
    let latest = reader.latest_samples(&["sandbox", "workspace-1"]);

    assert_eq!(latest.len(), 2, "unrequested scopes are not retained");
    let sandbox = &latest["sandbox"];
    assert_eq!(sandbox.ts, 300);
    assert_eq!(sandbox.metrics["cpu_usec"], 250);
    assert_eq!(sandbox.deltas["cpu_usec"], 100);
    assert_eq!(sandbox.sample_delta_ms, Some(100));
    let workspace = &latest["workspace-1"];
    assert_eq!(workspace.ts, 250);
    assert_eq!(workspace.metrics["disk_bytes"], 3);
    assert!(workspace.deltas.is_empty());
    assert_eq!(workspace.sample_delta_ms, Some(100));
}

#[test]
fn trace_builds_tree_with_offsets_resolving_out_of_order() {
    let dir = temp_dir("trace");
    let primary = dir.join("observability.ndjson");
    let rotated = dir.join("observability.ndjson.1");
    // Child + event appended BEFORE the parent span — resolution is by id.
    write_lines(
        &primary,
        &[
            json!({ "kind": "span", "ts": 60, "trace": "t", "span": "d-1", "parent": "d-0", "name": "command.exec", "dur_ms": 20.0, "status": "completed", "attrs": {} }),
            json!({ "kind": "event", "ts": 50, "trace": "t", "parent": "d-1", "name": "lease.acquired", "attrs": {} }),
            json!({ "kind": "span", "ts": 120, "trace": "t", "span": "d-0", "name": "daemon.dispatch", "dur_ms": 120.0, "status": "completed", "attrs": {} }),
        ],
    );

    let reader = Reader::new(primary, rotated);
    let forest = reader.trace("t");
    assert_eq!(forest.len(), 1, "one root");
    let root = &forest[0];
    assert_eq!(root.span.span, "d-0");
    assert_eq!(root.offset_ms, 0.0, "root starts at trace_start");
    assert_eq!(root.children.len(), 1);
    let child = &root.children[0];
    assert_eq!(child.span.span, "d-1");
    assert_eq!(
        child.offset_ms, 40.0,
        "child start offset = (ts - dur) - trace_start"
    );
    assert_eq!(
        child.events.len(),
        1,
        "event resolves under its parent span"
    );
    assert_eq!(child.events[0].event.name, "lease.acquired");
    assert_eq!(child.events[0].offset_ms, 50.0);
}

#[test]
fn raw_and_events_filter_by_kind_name_trace_since() {
    let dir = temp_dir("filters");
    let primary = dir.join("observability.ndjson");
    let rotated = dir.join("observability.ndjson.1");
    write_lines(
        &primary,
        &[
            json!({ "kind": "span", "ts": 10, "trace": "t1", "span": "d-0", "name": "command.exec", "dur_ms": 1.0, "status": "completed", "attrs": {} }),
            json!({ "kind": "event", "ts": 20, "trace": "t1", "parent": "d-0", "name": "lease.acquired", "attrs": { "layer_id": "l0" } }),
            json!({ "kind": "event", "ts": 30, "trace": "t2", "parent": "x-0", "name": "lease.released", "attrs": {} }),
        ],
    );

    let reader = Reader::new(primary, rotated);
    assert_eq!(
        reader
            .raw(RawFilter {
                kind: Some("event".to_owned()),
                ..Default::default()
            })
            .len(),
        2,
        "kind filter"
    );
    assert_eq!(
        reader
            .raw(RawFilter {
                trace: Some("t1".to_owned()),
                ..Default::default()
            })
            .len(),
        2,
        "trace filter spans kinds"
    );
    assert_eq!(
        reader
            .raw(RawFilter {
                since_ms: 25,
                ..Default::default()
            })
            .len(),
        1,
        "since filter"
    );

    let events = reader.events(RawFilter {
        name: Some("lease.acquired".to_owned()),
        ..Default::default()
    });
    assert_eq!(events.len(), 1, "events fold reuses parsed Event records");
    assert_eq!(events[0].attrs["layer_id"], "l0");
}
