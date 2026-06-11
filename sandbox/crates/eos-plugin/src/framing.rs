//! PPC frame encode/decode: the plugin channel's OWN copy of the daemon's
//! newline-delimited compact-JSON framing.
//!
//! No compiled wire code is shared across crate boundaries anymore; the PPC
//! deliberately reproduces the same bytes (`json.dumps(obj,
//! separators=(",",":")) + "\n"`, request key order `op, invocation_id,
//! args`), and the daemon decodes PPC frames with its own wire module. Any
//! drift surfaces in the plugin dispatch e2e tier.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

/// Maximum bytes in one PPC frame (mirrors the daemon request cap).
pub const MAX_PPC_FRAME_BYTES: usize = 16 * 1024 * 1024;

/// Encode/decode failures for the framed PPC channel.
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum ProtocolError {
    /// The frame was not valid UTF-8 JSON.
    #[error("bad json: {0}")]
    BadJson(#[from] serde_json::Error),
    /// The decoded value was not a JSON object.
    #[error("envelope must be a json object")]
    NotAnObject,
}

/// Request frame: `{op, invocation_id, args}` in exactly this key order.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Request {
    /// Op name (`plugin.<p>.<op>` or a reply/callback sentinel).
    pub op: String,
    /// Correlates a reply to its request (the PPC `message_id`).
    pub invocation_id: String,
    /// Opaque args object.
    pub args: Value,
}

/// One framed PPC message: a request shape, or any other JSON object (a
/// non-request frame is rejected by the PPC layer above).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Envelope {
    /// A request-shaped frame (has `op`).
    Request(Request),
    /// Anything else (replies in foreign shapes, raw responses).
    Other(Value),
}

/// Serialize an envelope as compact JSON plus a single trailing `\n`.
///
/// # Errors
/// Returns [`ProtocolError::BadJson`] when serde cannot serialize the frame.
pub fn encode(envelope: &Envelope) -> Result<Vec<u8>, ProtocolError> {
    let mut bytes = match envelope {
        Envelope::Request(request) => serde_json::to_vec(request)?,
        Envelope::Other(value) => serde_json::to_vec(value)?,
    };
    bytes.push(b'\n');
    Ok(bytes)
}

/// Decode one framed message; a trailing `\n` is tolerated.
///
/// # Errors
/// Returns [`ProtocolError::BadJson`] for invalid JSON and
/// [`ProtocolError::NotAnObject`] when the value is not a JSON object.
pub fn decode(bytes: &[u8]) -> Result<Envelope, ProtocolError> {
    let value: Value = serde_json::from_slice(bytes)?;
    let Some(object) = value.as_object() else {
        return Err(ProtocolError::NotAnObject);
    };
    if object.contains_key("op") {
        return Ok(Envelope::Request(serde_json::from_value(value)?));
    }
    Ok(Envelope::Other(value))
}

#[cfg(test)]
#[path = "../tests/unit/framing.rs"]
mod tests;
