//! Host-side conformance (SPEC §9.3): `host` encodes requests
//! that reproduce the frozen request fixtures byte-for-byte, with NO compiled
//! code shared with the box side — this is the drift defense for the
//! deliberately duplicated host wire vocabulary.
#![cfg(feature = "e2e-support")]

use serde_json::json;

use host::e2e_support::{
    encode_request_with_metadata, CONNECT_RETRY_DELAYS_S, DAEMON_AUTH_FIELD, DAEMON_PROTOCOL_FIELD,
    DAEMON_PROTOCOL_VERSION, MAX_REQUEST_BYTES,
};

/// The auth token is a TOP-LEVEL request field, never inside args.
#[test]
fn auth_token_is_stamped_top_level() {
    let encoded =
        encode_request_with_metadata("sandbox.runtime.ready", "i1", &json!({}), Some("tok-1"));
    let value: serde_json::Value = serde_json::from_slice(&encoded).expect("decode");
    assert_eq!(value[DAEMON_AUTH_FIELD], json!("tok-1"));
    assert!(value["args"].get(DAEMON_AUTH_FIELD).is_none());
}

/// Reserved wire-version metadata is owned by the host encoder.
#[test]
fn stamped_encoder_overwrites_caller_protocol_version() {
    let encoded = encode_request_with_metadata(
        "sandbox.runtime.ready",
        "i1",
        &json!({ DAEMON_PROTOCOL_FIELD: 999 }),
        None,
    );
    let value: serde_json::Value = serde_json::from_slice(&encoded).expect("decode");
    assert_eq!(
        value["args"][DAEMON_PROTOCOL_FIELD],
        json!(DAEMON_PROTOCOL_VERSION)
    );
}

/// The duplicated host-side limits match the frozen contract.
#[test]
fn host_wire_constants_match_frozen_contract() {
    assert_eq!(MAX_REQUEST_BYTES, 16 * 1024 * 1024);
    assert_eq!(CONNECT_RETRY_DELAYS_S, [0.25, 0.5, 1.0, 2.0]);
    assert_eq!(DAEMON_PROTOCOL_VERSION, 1);
    assert_eq!(DAEMON_PROTOCOL_FIELD, "_eos_daemon_protocol_version");
    assert_eq!(DAEMON_AUTH_FIELD, "_eos_daemon_auth_token");
}
