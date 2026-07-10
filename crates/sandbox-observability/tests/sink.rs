//! Sink: single-write append keeps lines intact under concurrent appenders, and
//! an over-cap line becomes a `{"_truncated": n}` marker (never a split line),
//! with the documented Span-nested / Sample-top-level asymmetry.

use std::borrow::Cow;
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use sandbox_observability::{Attrs, Record, Sample, Sink, Span, SpanStatus, MAX_LINE_BYTES};
use serde_json::{json, Value};

static NEXT: AtomicU64 = AtomicU64::new(0);

fn temp_log(label: &str) -> PathBuf {
    std::env::temp_dir()
        .join(format!(
            "sandbox-obs-sink-{label}-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ))
        .join("observability.ndjson")
}

fn attrs(value: Value) -> Attrs {
    value.as_object().cloned().unwrap_or_default()
}

#[test]
fn concurrent_appends_keep_every_line_intact() {
    let path = temp_log("concurrent");
    let sink = Arc::new(Sink::new(path.clone(), MAX_LINE_BYTES));
    let threads = 8;
    let per_thread = 64;

    std::thread::scope(|scope| {
        for thread in 0..threads {
            let sink = Arc::clone(&sink);
            scope.spawn(move || {
                for index in 0..per_thread {
                    let record = Record::Sample(Sample {
                        ts: i64::from(thread * per_thread + index),
                        scope: "sandbox".to_owned(),
                        metrics: attrs(json!({ "n": thread, "i": index })),
                    });
                    sink.append(&record).expect("append");
                }
            });
        }
    });

    let contents = fs::read_to_string(&path).expect("read log");
    let lines: Vec<&str> = contents.lines().collect();
    assert_eq!(lines.len() as i32, threads * per_thread, "no lines lost");
    for line in lines {
        serde_json::from_str::<Record>(line).expect("each line parses intact (not interleaved)");
    }

    let _ = fs::remove_dir_all(path.parent().expect("parent"));
}

#[test]
fn over_cap_span_truncates_attrs_in_place() {
    let path = temp_log("trunc-span");
    let sink = Sink::new(path.clone(), MAX_LINE_BYTES);
    let blob = "x".repeat(MAX_LINE_BYTES);
    sink.append(&Record::Span(Span {
        ts: 1,
        trace: "t".to_owned(),
        span: "d-0".to_owned(),
        parent: None,
        name: Cow::Borrowed("command.exec"),
        dur_ms: 0.0,
        status: SpanStatus::Completed,
        attrs: attrs(json!({ "blob": blob })),
    }))
    .expect("append");

    let contents = fs::read_to_string(&path).expect("read log");
    let lines: Vec<&str> = contents.lines().collect();
    assert_eq!(lines.len(), 1, "one line, never split");
    let value: Value = serde_json::from_str(lines[0]).expect("parses");
    assert!(
        value["attrs"]["_truncated"].is_number(),
        "Span marker nested under attrs"
    );
    assert!(
        value["attrs"].get("blob").is_none(),
        "oversized attr dropped wholesale"
    );
    assert!(lines[0].len() < MAX_LINE_BYTES, "truncated line is small");

    let _ = fs::remove_dir_all(path.parent().expect("parent"));
}

#[test]
fn over_cap_sample_truncates_metrics_at_top_level() {
    let path = temp_log("trunc-sample");
    let sink = Sink::new(path.clone(), MAX_LINE_BYTES);
    let blob = "x".repeat(MAX_LINE_BYTES);
    sink.append(&Record::Sample(Sample {
        ts: 1,
        scope: "sandbox".to_owned(),
        metrics: attrs(json!({ "blob": blob })),
    }))
    .expect("append");

    let contents = fs::read_to_string(&path).expect("read log");
    let value: Value =
        serde_json::from_str(contents.lines().next().expect("line")).expect("parses");
    assert!(
        value["_truncated"].is_number(),
        "flattened Sample marker lands at the top level"
    );
    assert!(value.get("blob").is_none());

    let _ = fs::remove_dir_all(path.parent().expect("parent"));
}
