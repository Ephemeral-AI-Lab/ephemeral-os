use sandbox_operation_contract::{OperationRequest, OperationResponse, OperationScope};
use sandbox_protocol::{
    daemon_readiness_request_line, decode_request_value, decode_response_line,
    encode_authenticated_request_line, encode_request_line, response_line, DAEMON_AUTH_FIELD,
    DAEMON_READINESS_OPERATION, DAEMON_READINESS_REQUEST_ID,
};
use serde_json::json;

#[test]
fn daemon_auth_field_uses_sandbox_name() {
    assert_eq!(DAEMON_AUTH_FIELD, "_sandbox_daemon_auth_token");
}

#[test]
fn decode_request_requires_object_args() {
    let error = decode_request_value(json!({
        "op": "exec_command",
        "request_id": "req-1",
        "scope": { "kind": "sandbox", "sandbox_id": "sbox-1" },
        "args": "bad",
    }))
    .expect_err("non-object args rejected");

    assert_eq!(error.kind(), "invalid_request");
    assert_eq!(error.message(), "args must be an object");
}

#[test]
fn decode_request_rejects_missing_scope() {
    let error = decode_request_value(json!({
        "op": "list_sandboxes",
        "request_id": "req-1",
        "args": {},
    }))
    .expect_err("missing scope rejected");

    assert_eq!(error.kind(), "invalid_request");
    assert_eq!(error.message(), "scope is required");
}

#[test]
fn decode_request_accepts_sandbox_scope() {
    let request = decode_request_value(json!({
        "op": "exec_command",
        "request_id": "req-1",
        "scope": { "kind": "sandbox", "sandbox_id": "sbox-1" },
        "args": {},
    }))
    .expect("request decodes");

    assert_eq!(request.scope, OperationScope::sandbox("sbox-1"));
}

#[test]
fn decode_request_rejects_empty_sandbox_scope_id() {
    let error = decode_request_value(json!({
        "op": "exec_command",
        "request_id": "req-1",
        "scope": { "kind": "sandbox", "sandbox_id": "" },
        "args": {},
    }))
    .expect_err("empty sandbox id rejected");

    assert_eq!(error.kind(), "invalid_request");
    assert_eq!(error.message(), "scope sandbox_id must be non-empty");
}

#[test]
fn request_encoding_preserves_wire_shape_and_newline() {
    let request = OperationRequest::new(
        "exec_command",
        "req-1",
        OperationScope::sandbox("sbox-1"),
        json!({"cmd": "pwd"}),
    );

    let line = encode_request_line(&request).expect("request encodes");

    assert!(line.ends_with(b"\n"));
    assert_eq!(
        serde_json::from_slice::<serde_json::Value>(&line).expect("valid json"),
        json!({
            "op": "exec_command",
            "request_id": "req-1",
            "scope": { "kind": "sandbox", "sandbox_id": "sbox-1" },
            "args": {"cmd": "pwd"}
        })
    );
}

#[test]
fn authenticated_request_encoding_adds_only_auth_field() {
    let request = OperationRequest::new(
        "exec_command",
        "req-1",
        OperationScope::sandbox("sbox-1"),
        json!({}),
    );

    let line = encode_authenticated_request_line(&request, DAEMON_AUTH_FIELD, "tok-1")
        .expect("authenticated request encodes");
    let value: serde_json::Value = serde_json::from_slice(&line).expect("valid json");

    assert_eq!(value[DAEMON_AUTH_FIELD], "tok-1");
    assert_eq!(value["op"], "exec_command");
    assert!(line.ends_with(b"\n"));
}

#[test]
fn response_codec_preserves_payload_owned_shape() {
    let value = json!({"status": "ok", "output": "hello"});
    let response = OperationResponse::from_json_value(value.clone());

    let line = response_line(&response);

    assert!(line.ends_with(b"\n"));
    assert_eq!(
        decode_response_line(&line)
            .expect("response decodes")
            .into_json_value(),
        value
    );
}

#[test]
fn response_codec_streams_raw_json_and_decodes_compatibly() {
    let response =
        OperationResponse::from_raw_json(r#"{"view":"events","events":[{"ts":1}]}"#.to_owned())
            .expect("raw response validates");

    let line = response_line(&response);

    assert_eq!(line, b"{\"view\":\"events\",\"events\":[{\"ts\":1}]}\n");
    assert_eq!(
        decode_response_line(&line)
            .expect("raw response decodes")
            .into_json_value()["events"][0]["ts"],
        1
    );
}

#[test]
fn readiness_handshake_is_canonical_and_authenticated() {
    let line = daemon_readiness_request_line("sbox-1", "tok-1").expect("handshake encodes");
    let value: serde_json::Value = serde_json::from_slice(&line).expect("valid json");

    assert_eq!(DAEMON_READINESS_OPERATION, "sandbox_daemon_ready");
    assert_eq!(DAEMON_READINESS_REQUEST_ID, "docker-readiness");
    assert_eq!(value["op"], DAEMON_READINESS_OPERATION);
    assert_eq!(value["request_id"], DAEMON_READINESS_REQUEST_ID);
    assert_eq!(value["scope"]["sandbox_id"], "sbox-1");
    assert_eq!(value[DAEMON_AUTH_FIELD], "tok-1");
    assert!(line.ends_with(b"\n"));
}
