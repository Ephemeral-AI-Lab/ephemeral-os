use daemon_rpc_protocol::{
    decode_request_object, encode_request, ArgsPresence, DaemonRpcAuth, DAEMON_AUTH_FIELD,
    DAEMON_FORWARD_AUTH_FIELD,
};
use serde_json::json;

#[test]
fn encode_request_stamps_auth_at_top_level() {
    let raw = encode_request(
        "sandbox.runtime.ready",
        "req-1",
        &json!({}),
        DaemonRpcAuth::Raw(Some("tok")),
    );
    let value: serde_json::Value = serde_json::from_slice(&raw).expect("request json");
    assert_eq!(value[DAEMON_AUTH_FIELD], json!("tok"));
    assert!(value["args"].get(DAEMON_AUTH_FIELD).is_none());

    let forward = encode_request(
        "sandbox.runtime.ready",
        "req-1",
        &json!({}),
        DaemonRpcAuth::Forward(Some("forward")),
    );
    let value: serde_json::Value = serde_json::from_slice(&forward).expect("request json");
    assert_eq!(value[DAEMON_FORWARD_AUTH_FIELD], json!("forward"));
    assert!(value["args"].get(DAEMON_FORWARD_AUTH_FIELD).is_none());
}

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
