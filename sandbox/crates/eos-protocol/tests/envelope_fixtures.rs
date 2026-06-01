//! Envelope wire fixtures: byte-stable round-trip for requests/errors,
//! canonical-equal (drop timings) for responses. Fixtures are immutable ground
//! truth from the live Python (`json.dumps(separators=(",",":")) + "\n"`).

use eos_protocol::canonical::canonicalize;
use eos_protocol::envelope::{decode, encode, Envelope};
use serde_json::Value;

macro_rules! fixture {
    ($name:literal) => {
        include_bytes!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/fixtures/envelopes/",
            $name
        ))
    };
}

/// Requests and error envelopes are byte-identity: decode -> encode == original.
#[test]
fn requests_and_errors_byte_stable() {
    let raws: &[&[u8]] = &[
        fixture!("read_file_request.json"),
        fixture!("heartbeat_request.json"),
        fixture!("readiness_request.json"),
        fixture!("error_unknown_op.json"),
        fixture!("error_request_too_large.json"),
    ];
    for raw in raws {
        let env = decode(raw).expect("decode fixture");
        match &env {
            Envelope::Request(_) | Envelope::Error(_) => {}
            Envelope::Response(_) => panic!("expected request/error, got response: {env:?}"),
        }
        let reencoded = encode(&env).expect("encode");
        assert_eq!(
            reencoded,
            raw.to_vec(),
            "byte-stable round-trip failed for fixture: {}",
            String::from_utf8_lossy(raw)
        );
    }
}

/// Responses are canonical-equal (the `timings`/pid/uptime allowlist is dropped).
#[test]
fn responses_canonical_stable() {
    let raws: &[&[u8]] = &[
        fixture!("read_file_response.json"),
        fixture!("heartbeat_response.json"),
        fixture!("readiness_response.json"),
    ];
    for raw in raws {
        let env = decode(raw).expect("decode response fixture");
        let value = match &env {
            Envelope::Response(v) => v.clone(),
            other => panic!("expected response, got {other:?}"),
        };
        // Re-encode then re-decode; the canonical form must be stable.
        let reencoded = encode(&env).expect("encode");
        let redecoded = decode(&reencoded).expect("decode roundtrip");
        let value2 = match redecoded {
            Envelope::Response(v) => v,
            other => panic!("expected response, got {other:?}"),
        };
        assert_eq!(canonicalize(&value), canonicalize(&value2));

        // And the canonical form drops timings entirely.
        let canon = canonicalize(&value);
        assert!(
            canon.get("timings").is_none(),
            "timings should be dropped by canonicalize"
        );
    }
}

/// The protocol-version field lives INSIDE args (documented, not gated).
#[test]
fn protocol_version_field_inside_args() {
    let raw = fixture!("read_file_request.json");
    let value: Value = serde_json::from_slice(raw).expect("parse read_file request fixture");
    assert!(value.get("_eos_daemon_protocol_version").is_none());
    assert_eq!(
        value["args"]["_eos_daemon_protocol_version"],
        Value::Number(1.into())
    );
}
