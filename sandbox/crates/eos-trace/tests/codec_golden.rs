use eos_trace::{
    decode_trace_batch, encode_trace_batch, EventRecord, RequestId, ResourceStats,
    ResourceStatsKind, SpanKind, SpanRecord, SpanUid, TraceBatch, TraceId, TraceLink,
    TraceLinkKind, TraceRecord,
};
use serde_json::json;

fn canonical_batch() -> TraceBatch {
    let trace_id = TraceId::parse("trace-codec").expect("trace id");
    let mut record = TraceRecord::new(trace_id, SpanUid::ROOT);
    record.request_id = Some(RequestId::parse("request-codec").expect("request id"));
    record.spans.push(SpanRecord::new(
        SpanUid::ROOT,
        None,
        "op_request",
        SpanKind::OpRequest,
        json!({"op":"sandbox.ready"}),
    ));
    record.events.push(EventRecord::new(
        SpanUid::ROOT,
        "dispatch_started",
        "daemon.dispatch",
        json!({"op_resolved": true}),
    ));
    record.links.push(TraceLink {
        kind: TraceLinkKind::Command,
        value: "cmd-1".to_owned(),
    });
    record.resources.push(
        ResourceStats::available(
            ResourceStatsKind::CgroupProcess,
            Some("after".to_owned()),
            "command.process.wait",
            7,
            1,
            json!({"cpu": {"usage_usec": 42}}),
        )
        .with_span_id(SpanUid::ROOT),
    );
    let mut batch = TraceBatch::single(record);
    batch.daemon_boot_id = Some("boot-codec".to_owned());
    batch
}

#[test]
fn round_trips_trace_batch_through_protobuf() {
    let batch = canonical_batch();
    let encoded = encode_trace_batch(&batch);
    let decoded = decode_trace_batch(&encoded).expect("decode encoded trace batch");

    assert_eq!(decoded.records, batch.records);
    assert_eq!(decoded.daemon_boot_id.as_deref(), Some("boot-codec"));
}

/// Schema-evolution gate: the committed populated fixture must keep decoding
/// to the same DTOs after any proto regeneration. Read at runtime so the
/// fixture can be regenerated without a compile dependency on its presence.
#[test]
fn decodes_committed_populated_fixture() {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/trace_batch_v1_populated.hex"
    );
    let hex = std::fs::read_to_string(path).expect("read committed populated fixture");
    let bytes = hex_to_bytes(hex.trim());
    let decoded = decode_trace_batch(&bytes).expect("decode populated fixture");
    let expected = canonical_batch();
    assert_eq!(decoded.records, expected.records);
    assert_eq!(decoded.daemon_boot_id, expected.daemon_boot_id);
    assert_eq!(decoded.dropped_traces, expected.dropped_traces);
}

#[test]
fn decodes_committed_v1_fixture() {
    let hex = include_str!("fixtures/trace_batch_v1.hex").trim();
    let bytes = hex_to_bytes(hex);
    let decoded = decode_trace_batch(&bytes).expect("decode v1 fixture");

    assert_eq!(decoded.dropped_traces, 7);
    assert!(decoded.records.is_empty());
}

fn hex_to_bytes(hex: &str) -> Vec<u8> {
    hex.as_bytes()
        .chunks_exact(2)
        .map(|chunk| {
            let text = std::str::from_utf8(chunk).expect("hex utf8");
            u8::from_str_radix(text, 16).expect("hex byte")
        })
        .collect()
}
