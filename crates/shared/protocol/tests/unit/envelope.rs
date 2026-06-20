use serde_json::{json, Value};

use super::*;
use crate::FaultDetails;

fn meta() -> ResponseMeta {
    ResponseMeta {
        op: "sandbox.runtime.ready".to_owned(),
        request_id: "req-1".to_owned(),
        ..ResponseMeta::default()
    }
}

fn six_envelopes() -> [OperationEnvelope<Value>; 6] {
    [
        OperationEnvelope::ok(json!({"ready": true}), meta()),
        OperationEnvelope::running(json!({"command_id": "cmd-1"}), meta()),
        OperationEnvelope::rejected_with_result(
            OperationFault::new("occ_conflict", "path contended"),
            json!({"exit_code": 0}),
            meta(),
        ),
        OperationEnvelope::cancelled(json!({"kill_reason": "cancelled"}), meta()),
        OperationEnvelope::timed_out(json!({"kill_reason": "timed_out"}), meta()),
        OperationEnvelope::error(
            OperationFault::internal("failed", FaultDetails::default()),
            meta(),
        ),
    ]
}

#[test]
fn serializes_each_status_with_one_discriminant() {
    let statuses = six_envelopes().map(|envelope| {
        serde_json::to_value(envelope)
            .expect("envelope serializes")
            .get("status")
            .and_then(Value::as_str)
            .expect("status string")
            .to_owned()
    });
    assert_eq!(
        statuses,
        [
            "ok",
            "running",
            "rejected",
            "cancelled",
            "timed_out",
            "error"
        ]
    );
}

#[test]
fn arms_carry_result_xor_error_by_construction() {
    for envelope in six_envelopes() {
        let status = envelope.status();
        let value = serde_json::to_value(&envelope).expect("envelope serializes");
        match status {
            OperationStatus::Ok
            | OperationStatus::Running
            | OperationStatus::Cancelled
            | OperationStatus::TimedOut => {
                assert!(value.get("result").is_some(), "{status:?} carries result");
                assert!(value.get("error").is_none(), "{status:?} has no error key");
            }
            OperationStatus::Rejected => {
                assert!(value.get("error").is_some(), "rejected carries fault");
                assert!(
                    value.get("result").is_some(),
                    "rejected keeps partial result facts"
                );
            }
            OperationStatus::Error => {
                assert!(value.get("error").is_some(), "error carries fault");
                assert!(value.get("result").is_none(), "error has no result key");
            }
        }
        assert!(value.get("meta").is_some(), "{status:?} carries meta");
        let roundtrip: OperationEnvelope<Value> =
            serde_json::from_value(value).expect("envelope deserializes");
        assert_eq!(roundtrip.status(), status, "round trip keeps the status");
    }
}

#[test]
fn meta_serializes_required_spec_fields() {
    let value = serde_json::to_value(OperationEnvelope::ok(json!({}), meta()))
        .expect("envelope serializes");
    let meta = value.get("meta").expect("meta object");
    for field in [
        "envelope_version",
        "op",
        "request_id",
        "workspace_route",
        "duration_ms",
        "modules_touched",
        "steps",
        "resource_summary",
        "warnings",
    ] {
        assert!(meta.get(field).is_some(), "meta.{field} is always present");
    }
    assert_eq!(meta["envelope_version"], 2);
    assert_eq!(meta["workspace_route"]["kind"], "none");
    assert!(meta.get("trace").is_none());
}

#[test]
fn meta_serializes_populated_trace_reference() {
    let value = serde_json::to_value(OperationEnvelope::ok(
        json!({}),
        ResponseMeta {
            trace: TraceRef {
                trace_id: "trace-1".to_owned(),
                request_id: Some("req-1".to_owned()),
                store: "local_sqlite".to_owned(),
                event_count: 3,
                degraded: false,
                root_span_id: None,
            },
            ..meta()
        },
    ))
    .expect("envelope serializes");
    let meta = value.get("meta").expect("meta object");
    assert_eq!(meta["trace"]["trace_id"], "trace-1");
    assert_eq!(meta["trace"]["request_id"], "req-1");
    assert_eq!(meta["trace"]["store"], "local_sqlite");
}
