use crate::daemon_wire::{
    decode_trace_sidecar_base64, read_response_line_with_limit, response_domain_status,
    response_envelope_status, response_fault_kind, response_is_accepted, response_status,
    take_trace_sidecar_checked, ClientError, TraceSidecarError, DAEMON_TRACE_SIDECAR_ENCODING,
    DAEMON_TRACE_SIDECAR_SCHEMA,
};
use std::io::BufReader;

use serde_json::json;

#[test]
fn reads_operation_envelope_statuses() {
    let cases = [
        (
            "envelope ok",
            json!({"status": "ok", "result": {"ready": true}, "meta": {}}),
            "ok",
            None,
            true,
            None,
        ),
        (
            "domain result running",
            json!({"status": "ok", "result": {"status": "running", "command_id": "cmd-1"}, "meta": {}}),
            "ok",
            Some("running"),
            true,
            None,
        ),
        (
            "envelope running",
            json!({"status": "running", "result": {"command_id": "cmd-1"}, "meta": {}}),
            "running",
            None,
            true,
            None,
        ),
        (
            "envelope error",
            json!({"status": "error", "error": {"kind": "internal_error", "message": "failed"}, "meta": {}}),
            "error",
            None,
            false,
            Some("internal_error"),
        ),
        (
            "envelope rejected without kind",
            json!({"status": "rejected", "error": {"message": "blocked"}, "meta": {}}),
            "rejected",
            None,
            false,
            None,
        ),
        (
            "invalid envelope status",
            json!({"status": "mystery", "meta": {}}),
            "error",
            None,
            false,
            None,
        ),
        (
            "missing envelope status",
            json!({"ready": true}),
            "error",
            None,
            false,
            Some("missing_status"),
        ),
    ];

    for (label, response, status, domain_status, accepted, kind) in cases {
        assert_eq!(
            response_status(&response),
            status,
            "{label}: response status"
        );
        assert_eq!(
            response_envelope_status(&response),
            status,
            "{label}: envelope status"
        );
        assert_eq!(
            response_domain_status(&response),
            domain_status,
            "{label}: domain status"
        );
        assert_eq!(
            response_is_accepted(&response),
            accepted,
            "{label}: accepted response"
        );
        assert_eq!(response_fault_kind(&response), kind, "{label}: fault kind");
    }
}

#[test]
fn decodes_trace_sidecar_base64() {
    assert_eq!(
        decode_trace_sidecar_base64("AQID").as_deref(),
        Some(&[1, 2, 3][..])
    );
    assert!(decode_trace_sidecar_base64("not base64").is_none());
}

#[test]
fn daemon_response_reads_are_bounded() {
    let mut reader = BufReader::new(&b"01234567890\n"[..]);
    let err = read_response_line_with_limit(&mut reader, 10)
        .expect_err("oversized daemon response rejected");

    assert!(
        matches!(err, ClientError::ResponseTooLarge { limit: 10 }),
        "{err:?}"
    );
}

#[test]
fn checked_sidecar_decoder_strips_and_reports_malformed_values() {
    let mut wrapped = json!({
        "_trace_events": {
            "schema": DAEMON_TRACE_SIDECAR_SCHEMA,
            "encoding": DAEMON_TRACE_SIDECAR_ENCODING,
            "data": "AQID",
        },
    });
    assert_eq!(
        take_trace_sidecar_checked(&mut wrapped)
            .expect("wrapped sidecar decodes")
            .as_deref(),
        Some(&[1, 2, 3][..])
    );
    assert!(wrapped.get("_trace_events").is_none());

    let mut bare_string = json!({"_trace_events": "AQID"});
    assert_eq!(
        take_trace_sidecar_checked(&mut bare_string),
        Err(TraceSidecarError::NonString)
    );
    assert!(bare_string.get("_trace_events").is_none());

    let mut invalid_base64 = json!({
        "_trace_events": {
            "schema": DAEMON_TRACE_SIDECAR_SCHEMA,
            "encoding": DAEMON_TRACE_SIDECAR_ENCODING,
            "data": "not base64",
        },
    });
    assert_eq!(
        take_trace_sidecar_checked(&mut invalid_base64),
        Err(TraceSidecarError::InvalidBase64)
    );
    assert!(invalid_base64.get("_trace_events").is_none());

    let mut invalid_envelope = json!({"_trace_events": {"batch": "AQID"}});
    assert_eq!(
        take_trace_sidecar_checked(&mut invalid_envelope),
        Err(TraceSidecarError::InvalidEnvelope)
    );
    assert!(invalid_envelope.get("_trace_events").is_none());

    let mut non_string = json!({"_trace_events": 42});
    assert_eq!(
        take_trace_sidecar_checked(&mut non_string),
        Err(TraceSidecarError::NonString)
    );
    assert!(non_string.get("_trace_events").is_none());

    let mut absent = json!({"success": true});
    assert_eq!(take_trace_sidecar_checked(&mut absent), Ok(None));
}
