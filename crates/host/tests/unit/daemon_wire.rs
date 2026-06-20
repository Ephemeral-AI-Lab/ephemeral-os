use crate::daemon_wire::{
    read_response_line_with_limit, response_domain_status, response_envelope_status,
    response_fault_kind, response_is_accepted, response_status, ClientError,
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
            json!({"status": "ok", "result": {"status": "running", "command_session_id": "cmd-1"}, "meta": {}}),
            "ok",
            Some("running"),
            true,
            None,
        ),
        (
            "envelope running",
            json!({"status": "running", "result": {"command_session_id": "cmd-1"}, "meta": {}}),
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
fn daemon_response_reads_are_bounded() {
    let mut reader = BufReader::new(&b"01234567890\n"[..]);
    let err = read_response_line_with_limit(&mut reader, 10)
        .expect_err("oversized daemon response rejected");

    assert!(
        matches!(err, ClientError::ResponseTooLarge { limit: 10 }),
        "{err:?}"
    );
}
