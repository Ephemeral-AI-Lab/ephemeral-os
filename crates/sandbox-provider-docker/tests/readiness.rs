use serde_json::Value;

#[path = "../src/readiness.rs"]
mod readiness;

use readiness::validate_readiness_response;

#[test]
fn readiness_request_line_is_sandbox_scoped_and_authenticated() {
    let line = sandbox_protocol::daemon_readiness_request_line("sbox-1", "tok-123")
        .expect("readiness request encodes");

    assert_eq!(
        line.last(),
        Some(&b'\n'),
        "request must be newline terminated"
    );
    let request: Value = serde_json::from_slice(&line).expect("request line is valid json");
    assert_eq!(request["op"], "sandbox_daemon_ready");
    assert_eq!(request["request_id"], "docker-readiness");
    assert_eq!(request["scope"]["kind"], "sandbox");
    assert_eq!(request["scope"]["sandbox_id"], "sbox-1");
    assert_eq!(request["args"], serde_json::json!({}));
    assert_eq!(request["_sandbox_daemon_auth_token"], "tok-123");
}

#[test]
fn validate_readiness_response_accepts_ready_matching_sandbox() {
    let response =
        b"{\"status\":\"ready\",\"sandbox_id\":\"sbox-1\",\"daemon\":\"sandbox-daemon\"}\n";

    validate_readiness_response(response, "sbox-1").expect("ready response for matching sandbox");
}

#[test]
fn validate_readiness_response_rejects_unexpected_status() {
    let response = b"{\"error\":{\"kind\":\"unauthorized\",\"message\":\"no\"}}\n";

    let error = validate_readiness_response(response, "sbox-1").expect_err("non-ready rejected");

    assert!(error.contains("ready"), "unexpected error: {error}");
}

#[test]
fn validate_readiness_response_rejects_sandbox_id_mismatch() {
    let response =
        b"{\"status\":\"ready\",\"sandbox_id\":\"other\",\"daemon\":\"sandbox-daemon\"}\n";

    let error =
        validate_readiness_response(response, "sbox-1").expect_err("mismatched sandbox rejected");

    assert!(error.contains("mismatch"), "unexpected error: {error}");
}

#[test]
fn validate_readiness_response_requires_newline_termination() {
    let response =
        b"{\"status\":\"ready\",\"sandbox_id\":\"sbox-1\",\"daemon\":\"sandbox-daemon\"}";

    let error = validate_readiness_response(response, "sbox-1")
        .expect_err("unterminated response rejected");

    assert!(error.contains("newline"), "unexpected error: {error}");
}

#[test]
fn validate_readiness_response_rejects_non_json() {
    let response = b"not json\n";

    let error = validate_readiness_response(response, "sbox-1").expect_err("non-json rejected");

    assert!(error.contains("decode"), "unexpected error: {error}");
}

#[test]
fn validate_readiness_response_rejects_empty() {
    validate_readiness_response(b"", "sbox-1").expect_err("empty response rejected");
}
