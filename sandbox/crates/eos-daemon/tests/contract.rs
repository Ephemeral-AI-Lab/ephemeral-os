//! Daemon conformance (SPEC §9.2): envelope wire fixtures decode byte-stably
//! for requests/errors and canonical-equal (drop timings/daemon_pid/uptime_s)
//! for responses. Fixtures are immutable ground truth from the live runtime
//! (`json.dumps(separators=(",",":")) + "\n"`).

use eos_daemon::wire::canonical::canonicalize;
use eos_daemon::wire::envelope::{decode, encode, Envelope};
use serde_json::Value;

type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

macro_rules! fixture {
    ($name:literal) => {
        include_bytes!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../contract/fixtures/envelopes/",
            $name
        ))
    };
}

/// Requests and error envelopes are byte-identity: decode -> encode == original.
#[test]
fn requests_and_errors_byte_stable() -> TestResult {
    let raws: &[&[u8]] = &[
        fixture!("read_file_request.json"),
        fixture!("heartbeat_request.json"),
        fixture!("readiness_request.json"),
        fixture!("error_unknown_op.json"),
        fixture!("error_request_too_large.json"),
    ];
    for raw in raws {
        let env = decode(raw)?;
        match &env {
            Envelope::Request(_) | Envelope::Error(_) => {}
            Envelope::Response(_) => {
                return Err(std::io::Error::other(format!(
                    "expected request/error, got response: {env:?}"
                ))
                .into());
            }
        }
        let reencoded = encode(&env)?;
        assert_eq!(
            reencoded,
            raw.to_vec(),
            "byte-stable round-trip failed for fixture: {}",
            String::from_utf8_lossy(raw)
        );
    }
    Ok(())
}

/// Responses are canonical-equal (the `timings`/pid/uptime allowlist is dropped).
#[test]
fn responses_canonical_stable() -> TestResult {
    let raws: &[&[u8]] = &[
        fixture!("read_file_response.json"),
        fixture!("heartbeat_response.json"),
        fixture!("readiness_response.json"),
    ];
    for raw in raws {
        let env = decode(raw)?;
        let value = match &env {
            Envelope::Response(v) => v.clone(),
            other => {
                return Err(
                    std::io::Error::other(format!("expected response, got {other:?}")).into(),
                );
            }
        };
        // Re-encode then re-decode; the canonical form must be stable.
        let reencoded = encode(&env)?;
        let redecoded = decode(&reencoded)?;
        let value2 = match redecoded {
            Envelope::Response(v) => v,
            other => {
                return Err(
                    std::io::Error::other(format!("expected response, got {other:?}")).into(),
                );
            }
        };
        assert_eq!(canonicalize(&value), canonicalize(&value2));

        // And the canonical form drops timings entirely.
        let canon = canonicalize(&value);
        assert!(
            canon.get("timings").is_none(),
            "timings should be dropped by canonicalize"
        );
    }
    Ok(())
}

/// The protocol-version field lives INSIDE args (documented, not gated).
#[test]
fn protocol_version_field_inside_args() -> TestResult {
    let raw = fixture!("read_file_request.json");
    let value: Value = serde_json::from_slice(raw)?;
    assert!(value.get("_eos_daemon_protocol_version").is_none());
    assert_eq!(
        value["args"]["_eos_daemon_protocol_version"],
        Value::Number(1.into())
    );
    Ok(())
}
