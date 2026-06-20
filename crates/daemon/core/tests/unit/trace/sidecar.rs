use base64::Engine as _;
use serde_json::{json, Value};
use trace::{
    decode_trace_batch, DetailBudget, SpanKind, TRACE_SIDECAR_ENCODING, TRACE_SIDECAR_FIELD,
    TRACE_SIDECAR_SCHEMA,
};

use super::build::attach_request_sidecar;
use crate::trace::{now_ms, RequestTraceFacts};
use crate::wire::RequestTraceContext;

fn trace_sidecar_bytes(response: &Value) -> Option<Vec<u8>> {
    let sidecar = response.get(TRACE_SIDECAR_FIELD)?;
    let encoded = match sidecar {
        Value::Object(object) => {
            if object.get("schema").and_then(Value::as_str) != Some(TRACE_SIDECAR_SCHEMA) {
                return None;
            }
            if object.get("encoding").and_then(Value::as_str) != Some(TRACE_SIDECAR_ENCODING) {
                return None;
            }
            object.get("data").and_then(Value::as_str)?
        }
        _ => return None,
    };
    base64::engine::general_purpose::STANDARD
        .decode(encoded)
        .ok()
}

fn request_trace_context() -> RequestTraceContext {
    RequestTraceContext {
        trace_id: "trace-envelope-meta".to_owned(),
        request_id: "request-envelope-meta".to_owned(),
        parent_span_id: None,
        link_hints: Vec::new(),
        capture_budget_version: 1,
    }
}

fn request_trace_facts() -> RequestTraceFacts {
    RequestTraceFacts {
        connection_id: "daemon-conn-envelope-meta".to_owned(),
        accepted_at_unix_ms: now_ms(),
        listener_kind: "tcp",
        peer_addr: Some("127.0.0.1:51000".to_owned()),
        local_addr: Some("127.0.0.1:50000".to_owned()),
        is_tcp: true,
        request_bytes: 128,
        read_duration_us: 9,
        auth_required: true,
        auth_ok: true,
        protocol_version: Some(1),
    }
}

#[test]
fn request_sidecar_stamps_envelope_meta_from_fixed_record() {
    let trace = request_trace_context();
    let facts = request_trace_facts();
    let response = attach_request_sidecar(
        json!({"status": "ok", "result": {"published": true}, "meta": {}}),
        Some(&trace),
        "sandbox.command.exec",
        &facts,
    );

    assert_eq!(response["status"], "ok");
    assert_eq!(response["meta"]["op"], "sandbox.command.exec");
    assert_eq!(response["meta"]["request_id"], "request-envelope-meta");
    assert_eq!(response["meta"]["trace"]["trace_id"], "trace-envelope-meta");
    assert_eq!(
        response["meta"]["trace"]["request_id"],
        "request-envelope-meta"
    );
    assert_eq!(response["meta"]["trace"]["store"], "pending_host_ingest");
    assert_eq!(response["meta"]["workspace_route"]["kind"], "none");
    assert_eq!(
        response["meta"]["workspace_route"]["reason"],
        "no_route_recorded"
    );
    assert_eq!(
        response[TRACE_SIDECAR_FIELD]["schema"],
        TRACE_SIDECAR_SCHEMA
    );
    assert_eq!(
        response[TRACE_SIDECAR_FIELD]["encoding"],
        TRACE_SIDECAR_ENCODING
    );
    assert!(response[TRACE_SIDECAR_FIELD]["data"]
        .as_str()
        .is_some_and(|data| !data.is_empty()));
}

#[test]
fn request_sidecar_contains_only_fixed_request_spans() {
    let trace = request_trace_context();
    let facts = request_trace_facts();
    let response = attach_request_sidecar(
        json!({"status": "ok"}),
        Some(&trace),
        "sandbox.command.exec",
        &facts,
    );
    let batch = decode_trace_batch(&trace_sidecar_bytes(&response).expect("trace sidecar bytes"))
        .expect("trace batch decodes");
    let record = batch.records.first().expect("request trace record");

    let span_kinds: Vec<_> = record.spans.iter().map(|span| span.kind).collect();
    assert_eq!(
        span_kinds,
        vec![
            SpanKind::OpRequest,
            SpanKind::DaemonTransport,
            SpanKind::Dispatch,
            SpanKind::Operation,
        ]
    );
    assert!(record.resources.is_empty());
    assert!(!record.truncated);
    assert_eq!(record.dropped_children, 0);
    assert!(
        trace::codec::encoded_trace_record_len(record) <= DetailBudget::SidecarRecord.bytes(),
        "fixed request record fits the 64 KiB sidecar budget"
    );
}

#[test]
fn request_sidecar_records_core_transport_dispatch_and_route_events() {
    let trace = request_trace_context();
    let facts = request_trace_facts();
    let response = attach_request_sidecar(
        json!({"status": "ok"}),
        Some(&trace),
        "sandbox.command.exec",
        &facts,
    );
    let batch = decode_trace_batch(&trace_sidecar_bytes(&response).expect("trace sidecar bytes"))
        .expect("trace batch decodes");
    let record = batch.records.first().expect("request trace record");

    assert!(record
        .events
        .iter()
        .any(|event| event.module == "daemon.transport" && event.name == "accepted"));
    assert!(record
        .events
        .iter()
        .any(|event| event.module == "daemon.dispatch" && event.name == "dispatch_started"));
    let route = record
        .events
        .iter()
        .find(|event| event.module == "workspace.route" && event.name == "route_selected")
        .expect("workspace route event");
    assert_eq!(route.details.value["kind"], "none");
    assert_eq!(route.details.value["reason"], "no_route_recorded");
}
