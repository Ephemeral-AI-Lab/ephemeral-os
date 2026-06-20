use serde_json::json;

#[test]
fn parse_request_preserves_request_fields() {
    let args = json!({
        "command": "echo hi",
        "payload": ["large-ish", "owned", "args"],
    });
    let request = json!({
        "op": "command.exec",
        "request_id": "req-1",
        "args": args.clone(),
    });

    let (op, request_id, parsed_args) = parse_request(request).expect("valid request parses");

    assert_eq!(op, "command.exec");
    assert_eq!(request_id, "req-1");
    assert_eq!(parsed_args, args);
}

#[test]
fn parse_request_rejects_non_object_args() {
    let request = json!({
        "op": "command.exec",
        "request_id": "req-1",
        "args": "not an object",
    });

    let response = parse_request(request).expect_err("non-object args rejected");

    assert_eq!(response["status"], "error");
    assert_eq!(response["error"]["kind"], "invalid_request");
    assert_eq!(response["error"]["message"], "args must be an object");
}
