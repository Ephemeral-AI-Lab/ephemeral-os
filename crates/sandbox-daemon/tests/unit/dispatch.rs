use serde_json::json;

#[test]
fn decode_request_preserves_request_fields() {
    let args = json!({
        "command": "echo hi",
        "payload": ["large-ish", "owned", "args"],
    });
    let request = json!({
        "op": "command.exec",
        "request_id": "req-1",
        "args": args.clone(),
    });

    let parsed = decode_request(request).expect("valid request parses");

    assert_eq!(parsed.op, "command.exec");
    assert_eq!(parsed.request_id, "req-1");
    assert_eq!(parsed.args, args);
}

#[test]
fn decode_request_rejects_non_object_args() {
    let request = json!({
        "op": "command.exec",
        "request_id": "req-1",
        "args": "not an object",
    });

    let response = decode_request(request).expect_err("non-object args rejected");

    assert_eq!(response["error"]["kind"], "invalid_request");
    assert_eq!(response["error"]["message"], "args must be an object");
}
