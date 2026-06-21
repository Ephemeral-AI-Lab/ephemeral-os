use serde_json::json;

#[test]
fn decode_request_preserves_request_fields() {
    let args = json!({
        "command": "echo hi",
        "payload": ["large-ish", "owned", "args"],
    });
    let request = json!({
        "op": "exec_command",
        "request_id": "req-1",
        "scope": {
            "kind": "sandbox",
            "sandbox_id": "sbox-1"
        },
        "args": args.clone(),
    });

    let parsed = decode_request(request).expect("valid request parses");

    assert_eq!(parsed.op, "exec_command");
    assert_eq!(parsed.request_id, "req-1");
    assert_eq!(
        parsed.scope,
        sandbox_protocol::OperationScope::sandbox("sbox-1")
    );
    assert_eq!(parsed.args, args);
}

#[test]
fn decode_request_rejects_missing_scope() {
    let request = json!({
        "op": "exec_command",
        "request_id": "req-1",
        "args": {},
    });

    let response = decode_request(request).expect_err("missing scope rejected");

    assert_eq!(response["error"]["kind"], "invalid_request");
    assert_eq!(response["error"]["message"], "scope is required");
}

#[test]
fn decode_request_rejects_non_object_args() {
    let request = json!({
        "op": "exec_command",
        "request_id": "req-1",
        "scope": {
            "kind": "sandbox",
            "sandbox_id": "sbox-1"
        },
        "args": "not an object",
    });

    let response = decode_request(request).expect_err("non-object args rejected");

    assert_eq!(response["error"]["kind"], "invalid_request");
    assert_eq!(response["error"]["message"], "args must be an object");
}
