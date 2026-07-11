//! Record serde: internal `kind` tag (sibling field), nested `attrs` vs flattened
//! `metrics`, `exit_code` as an attr, owned `name` on read, and the layerstack
//! slice's on-disk sample bytes still parsing.

use std::borrow::Cow;

use sandbox_observability_telemetry::{Attrs, Event, Record, Sample, Span, SpanStatus};
use serde_json::{json, Value};

fn attrs(value: Value) -> Attrs {
    value.as_object().cloned().unwrap_or_default()
}

#[test]
fn span_round_trips_internally_tagged_with_nested_attrs() {
    let record = Record::Span(Span {
        ts: 1_000,
        trace: "req-1".to_owned(),
        span: "d-0".to_owned(),
        parent: None,
        name: Cow::Borrowed("daemon.dispatch"),
        dur_ms: 12.5,
        status: SpanStatus::Completed,
        attrs: attrs(json!({ "op": "exec_command" })),
    });

    let line = serde_json::to_string(&record).expect("serialize");
    let value: Value = serde_json::from_str(&line).expect("as value");
    assert_eq!(value["kind"], "span", "kind rides as a sibling field");
    assert_eq!(value["attrs"]["op"], "exec_command", "attrs nested");
    assert!(value.get("parent").is_none(), "root parent omitted: {line}");

    let parsed: Record = serde_json::from_str(&line).expect("round-trip");
    assert_eq!(parsed, record);
    let Record::Span(parsed) = parsed else {
        panic!("expected span");
    };
    assert!(
        matches!(parsed.name, Cow::Owned(_)),
        "name deserializes to owned"
    );
}

#[test]
fn span_exit_code_and_status_round_trip_as_fields_and_attrs() {
    let record = Record::Span(Span {
        ts: 5,
        trace: "t".to_owned(),
        span: "d-1".to_owned(),
        parent: Some("d-0".to_owned()),
        name: Cow::Borrowed("command.exec"),
        dur_ms: 1.0,
        status: SpanStatus::Error,
        attrs: attrs(json!({ "exit_code": 137 })),
    });

    let line = serde_json::to_string(&record).expect("serialize");
    let value: Value = serde_json::from_str(&line).expect("as value");
    assert_eq!(value["attrs"]["exit_code"], 137, "exit_code is an attr");
    assert_eq!(value["status"], "error");
    assert_eq!(value["parent"], "d-0");
    assert_eq!(
        serde_json::from_str::<Record>(&line).expect("round-trip"),
        record
    );
}

#[test]
fn event_round_trips() {
    let record = Record::Event(Event {
        ts: 9,
        trace: "t".to_owned(),
        parent: Some("d-0".to_owned()),
        name: Cow::Borrowed("lease.acquired"),
        attrs: attrs(json!({ "layer_id": "l0" })),
    });

    let line = serde_json::to_string(&record).expect("serialize");
    let value: Value = serde_json::from_str(&line).expect("as value");
    assert_eq!(value["kind"], "event");
    assert_eq!(value["attrs"]["layer_id"], "l0");
    assert_eq!(
        serde_json::from_str::<Record>(&line).expect("round-trip"),
        record
    );
}

#[test]
fn sample_metrics_flatten_to_top_level() {
    let record = Record::Sample(Sample {
        ts: 3,
        scope: "sandbox".to_owned(),
        metrics: attrs(json!({ "cpu_usec": 100, "mem_cur": 2048 })),
    });

    let line = serde_json::to_string(&record).expect("serialize");
    let value: Value = serde_json::from_str(&line).expect("as value");
    assert_eq!(value["kind"], "sample");
    assert_eq!(value["cpu_usec"], 100, "metrics flatten to the top level");
    assert_eq!(value["mem_cur"], 2048);
    assert!(value.get("metrics").is_none(), "metrics not nested");
    assert!(value.get("trace").is_none(), "samples carry no trace");
    assert_eq!(
        serde_json::from_str::<Record>(&line).expect("round-trip"),
        record
    );
}

#[test]
fn layerstack_slice_stack_sample_still_parses() {
    // The exact ad-hoc JSON the layerstack slice wrote, now typed as a Sample.
    let line = r#"{"ts":1700,"kind":"sample","scope":"stack","layer_count":3,"layers_bytes":4096,"active_leases":2}"#;
    let record: Record = serde_json::from_str(line).expect("legacy stack sample parses");
    let Record::Sample(sample) = record else {
        panic!("expected sample");
    };
    assert_eq!(sample.ts, 1700);
    assert_eq!(sample.scope, "stack");
    assert_eq!(sample.metrics["layer_count"], 3);
    assert_eq!(sample.metrics["layers_bytes"], 4096);
    assert_eq!(sample.metrics["active_leases"], 2);
}
