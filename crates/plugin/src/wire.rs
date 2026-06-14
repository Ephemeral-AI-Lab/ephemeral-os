//! PPC wire-message encode/decode: the plugin channel's OWN copy of the daemon's
//! newline-delimited compact-JSON request shape.
//!
//! No compiled wire code is shared across crate boundaries anymore; the PPC
//! deliberately reproduces the same bytes (`json.dumps(obj,
//! separators=(",",":")) + "\n"`, request key order `op, invocation_id,
//! args`), and the daemon decodes PPC messages with its own wire module. Any
//! drift surfaces in the plugin dispatch e2e tier.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

/// Maximum bytes in one PPC wire message (mirrors the daemon request cap).
pub const MAX_PPC_MESSAGE_BYTES: usize = 16 * 1024 * 1024;

/// Encode/decode failures for the PPC wire boundary.
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum WireError {
    /// The message was not valid UTF-8 JSON.
    #[error("bad json: {0}")]
    BadJson(#[from] serde_json::Error),
    /// The decoded value was not a JSON object.
    #[error("message must be a json object")]
    NotAnObject,
}

/// Request-shaped wire message: `{op, invocation_id, args}` in exactly this key
/// order.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RequestMessage {
    /// Op name (`plugin.<p>.<op>` or a reply/callback sentinel).
    pub op: String,
    /// Correlates a reply to its request (the PPC `message_id`).
    pub invocation_id: String,
    /// Opaque args object.
    pub args: Value,
}

/// One PPC wire message: a request shape, or any other JSON object.
///
/// Non-request messages are rejected by the PPC layer above.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Message {
    /// A request-shaped message (has `op`).
    Request(RequestMessage),
    /// Anything else (replies in foreign shapes, raw responses).
    Other(Value),
}

/// Serialize a message as compact JSON plus a single trailing `\n`.
///
/// # Errors
/// Returns [`WireError::BadJson`] when serde cannot serialize the message.
pub fn encode(message: &Message) -> Result<Vec<u8>, WireError> {
    let mut bytes = match message {
        Message::Request(request) => serde_json::to_vec(request)?,
        Message::Other(value) => serde_json::to_vec(value)?,
    };
    bytes.push(b'\n');
    Ok(bytes)
}

/// Decode one newline-delimited message; a trailing `\n` is tolerated.
///
/// # Errors
/// Returns [`WireError::BadJson`] for invalid JSON and
/// [`WireError::NotAnObject`] when the value is not a JSON object.
pub fn decode(bytes: &[u8]) -> Result<Message, WireError> {
    let value: Value = serde_json::from_slice(bytes)?;
    let Some(object) = value.as_object() else {
        return Err(WireError::NotAnObject);
    };
    if object.contains_key("op") {
        return Ok(Message::Request(serde_json::from_value(value)?));
    }
    Ok(Message::Other(value))
}

#[cfg(test)]
#[path = "../tests/unit/wire.rs"]
mod tests;
