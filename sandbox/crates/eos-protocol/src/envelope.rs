//! Newline-delimited JSON wire envelopes.
//!
//! Invariant: one compact JSON object per message + a single trailing `\n`
//! (`json.dumps(obj, separators=(",",":")) + "\n"`). [`encode`]/[`decode`] are
//! byte-stable for requests and error envelopes; responses are heterogeneous
//! `Value`s compared at the canonical bar (see [`crate::canonical`]).
//!
//! The protocol-version field `_eos_daemon_protocol_version` lives INSIDE `args`
//! and the daemon NEVER reads it (an inert versioning hook). We reproduce its
//! presence but do not validate it.
//! `// PORT backend/src/sandbox/daemon/rpc/server.py:74-75,133`
//! `// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:215-229`

use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

/// Encode/decode failures for the framed wire protocol. Distinct from the wire
/// [`ErrorKind`] (which is daemon policy, not a transport parse failure).
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum ProtocolError {
    /// The request line was not valid UTF-8 JSON.
    #[error("bad json: {0}")]
    BadJson(#[from] serde_json::Error),
    /// The decoded value was not a JSON object.
    #[error("envelope must be a json object")]
    NotAnObject,
}

/// Request envelope (host -> daemon): `{op, invocation_id, args}`. Field order on
/// the wire is exactly this (top-level keys are not sorted by the daemon).
/// `// PORT backend/src/sandbox/host/daemon_client.py:114-117`
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Request {
    pub op: String,
    pub invocation_id: String,
    pub args: Value,
}

/// Daemon error envelope (`success:false`). `warnings`/`timings` are always
/// `[]`/`{}` at the builder.
/// `// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:215-229 — _error_envelope`
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ErrorEnvelope {
    pub success: bool,
    #[serde(default)]
    pub warnings: Vec<Value>,
    #[serde(default)]
    pub timings: serde_json::Map<String, Value>,
    pub error: ErrorBody,
}

/// The `error` body of an [`ErrorEnvelope`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ErrorBody {
    pub kind: ErrorKind,
    pub message: String,
    #[serde(default)]
    pub details: Value,
}

/// Verified daemon error `kind` values. Serialized snake_case on the wire.
/// `// PORT backend/src/sandbox/daemon/rpc/dispatcher.py:147-273`
/// `// PORT backend/src/sandbox/daemon/rpc/server.py:104-120`
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum ErrorKind {
    /// `op` missing/non-string/empty, or `args` present but not a dict.
    InvalidEnvelope,
    /// Request line was not valid UTF-8 JSON.
    BadJson,
    /// Request line exceeded `MAX_REQUEST_BYTES`.
    RequestTooLarge,
    /// TCP only: configured auth token did not match.
    Unauthorized,
    /// `op` not registered in the daemon op table.
    UnknownOp,
    /// A handler raised; `details.error_id` carries a uuid4 hex.
    InternalError,
    /// Handler/gate policy refusal.
    Forbidden,
    /// Refused because an isolated workspace is active for this agent.
    ForbiddenInIsolatedWorkspace,
    /// Refused because a lifecycle operation is in progress.
    LifecycleInProgress,
}

/// A framed wire message: a request, an error envelope, or any response
/// `Value`. Untagged: a request has `op`; an error has `success:false` + `error`;
/// any other object is a response.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Envelope {
    /// Host -> daemon request.
    Request(Request),
    /// Daemon -> host error envelope.
    Error(ErrorEnvelope),
    /// Daemon -> host response (heterogeneous; compared canonically).
    Response(Value),
}

/// Serialize an envelope as compact JSON plus a single trailing `\n`.
/// `serde_json` compact formatting matches the daemon for these ASCII payloads;
/// `args` key order is preserved (the `preserve_order` feature is required).
pub fn encode(envelope: &Envelope) -> Result<Vec<u8>, ProtocolError> {
    let mut bytes = serde_json::to_vec(envelope)?;
    bytes.push(b'\n');
    Ok(bytes)
}

/// Decode one framed message. A trailing `\n` (and surrounding whitespace) is
/// tolerated; the body must be a single JSON object.
pub fn decode(bytes: &[u8]) -> Result<Envelope, ProtocolError> {
    let value: Value = serde_json::from_slice(bytes)?;
    if !value.is_object() {
        return Err(ProtocolError::NotAnObject);
    }
    // Disambiguate so a request never deserializes as a bare `Response(Value)`.
    let obj = value.as_object().ok_or(ProtocolError::NotAnObject)?;
    if obj.contains_key("op") {
        let req: Request = serde_json::from_value(value)?;
        return Ok(Envelope::Request(req));
    }
    if obj.get("success") == Some(&Value::Bool(false)) && obj.contains_key("error") {
        let err: ErrorEnvelope = serde_json::from_value(value)?;
        return Ok(Envelope::Error(err));
    }
    Ok(Envelope::Response(value))
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn error_kind_snake_case_wire() {
        let v = serde_json::to_value(ErrorKind::ForbiddenInIsolatedWorkspace).unwrap();
        assert_eq!(
            v,
            Value::String("forbidden_in_isolated_workspace".to_owned())
        );
        assert_eq!(
            serde_json::to_value(ErrorKind::UnknownOp).unwrap(),
            Value::String("unknown_op".to_owned())
        );
    }

    #[test]
    fn encode_appends_single_newline() {
        let env = Envelope::Response(serde_json::json!({"success": true, "touched": 0}));
        let bytes = encode(&env).unwrap();
        assert_eq!(*bytes.last().unwrap(), b'\n');
        assert_ne!(bytes[bytes.len() - 2], b'\n');
    }

    #[test]
    fn request_args_order_preserved_roundtrip() {
        let raw = b"{\"op\":\"x\",\"invocation_id\":\"i\",\"args\":{\"z\":1,\"a\":2,\"_eos_daemon_protocol_version\":1}}\n";
        let env = decode(raw).unwrap();
        assert!(matches!(env, Envelope::Request(_)));
        assert_eq!(encode(&env).unwrap(), raw);
    }

    // Build arbitrary JSON values with only finite numbers (NaN/Inf are not JSON).
    fn arb_json() -> impl Strategy<Value = Value> {
        let leaf = prop_oneof![
            Just(Value::Null),
            any::<bool>().prop_map(Value::Bool),
            any::<i64>().prop_map(|n| Value::Number(n.into())),
            ".*".prop_map(Value::String),
        ];
        leaf.prop_recursive(4, 32, 6, |inner| {
            prop_oneof![
                prop::collection::vec(inner.clone(), 0..6).prop_map(Value::Array),
                prop::collection::vec(("[a-z]{1,6}", inner), 0..6)
                    .prop_map(|kvs| { Value::Object(kvs.into_iter().collect()) }),
            ]
        })
    }

    proptest! {
        #[test]
        fn decode_encode_roundtrips_requests(op in "[a-z.]{1,12}", id in "[a-z0-9]{0,16}", args in arb_json()) {
            let args = if args.is_object() { args } else { serde_json::json!({"v": args}) };
            let env = Envelope::Request(Request { op, invocation_id: id, args });
            let bytes = encode(&env).unwrap();
            let back = decode(&bytes).unwrap();
            prop_assert_eq!(env, back);
        }
    }
}
