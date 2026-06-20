use sandbox_protocol::{decode_request_object, ArgsPresence, OperationScope};
use serde_json::json;

#[test]
fn decode_request_requires_object_args_when_present() {
    let value = json!({
        "op": "exec_command",
        "request_id": "req-1",
        "args": "bad",
    });
    let object = value.as_object().expect("object").clone();
    let err = decode_request_object(object, ArgsPresence::Required)
        .expect_err("non-object args rejected");
    assert_eq!(err.kind(), "invalid_request");
    assert_eq!(err.message(), "args must be an object");
}

#[test]
fn decode_request_defaults_missing_scope_to_system() {
    let value = json!({
        "op": "list_sandboxes",
        "request_id": "req-1",
        "args": {},
    });
    let object = value.as_object().expect("object").clone();
    let request =
        decode_request_object(object, ArgsPresence::Required).expect("request should decode");

    assert_eq!(request.scope, OperationScope::System);
}

#[test]
fn decode_request_accepts_sandbox_scope() {
    let value = json!({
        "op": "exec_command",
        "request_id": "req-1",
        "scope": {
            "kind": "sandbox",
            "sandbox_id": "sbox-1"
        },
        "args": {},
    });
    let object = value.as_object().expect("object").clone();
    let request =
        decode_request_object(object, ArgsPresence::Required).expect("request should decode");

    assert_eq!(
        request.scope,
        OperationScope::Sandbox {
            sandbox_id: "sbox-1".to_owned()
        }
    );
}

#[test]
fn decode_request_rejects_empty_sandbox_scope_id() {
    let value = json!({
        "op": "exec_command",
        "request_id": "req-1",
        "scope": {
            "kind": "sandbox",
            "sandbox_id": ""
        },
        "args": {},
    });
    let object = value.as_object().expect("object").clone();
    let err = decode_request_object(object, ArgsPresence::Required)
        .expect_err("empty sandbox id rejected");

    assert_eq!(err.kind(), "invalid_request");
    assert_eq!(err.message(), "scope sandbox_id must be non-empty");
}
