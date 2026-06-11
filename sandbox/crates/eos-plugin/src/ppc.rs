//! PPC (plugin-process channel): the bidirectional message-id'd request/reply
//! channel that replaces the in-process dynamic loading handler call.
//!
//! # Invariant
//!
//! The PPC reuses the daemon's newline-delimited compact-JSON framing — one
//! [`crate::framing::Envelope`] per message, a single trailing `\n` — over an
//! `AF_UNIX` socket to the daemon-managed service process. It is BIDIRECTIONAL and
//! message-id'd: plugin operation serialization is forbidden, so the daemon
//! multiplexes many in-flight ops over one service connection, and the
//! self-managed mode lets the plugin call BACK to the daemon (the OCC commit
//! callback) on the same channel. The `message_id` correlates a reply to its
//! request and is carried as the envelope's `invocation_id` so the existing
//! [`crate::framing::encode`]/[`crate::framing::decode`] framing applies unchanged
//! (no second wire format). Callback request bodies should include
//! `parent_message_id` so the daemon can route callback replies while many
//! callback-capable plugin ops are in flight on the same socket.
//!

use crate::framing::{decode, encode, Envelope, ProtocolError, Request};
use serde_json::json;

use crate::error::PluginError;

/// Direction of a PPC message on the bidirectional channel.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PpcDirection {
    /// Request frame. Daemon -> service invokes a plugin op; service -> daemon
    /// invokes a daemon callback such as self-managed OCC publish.
    Request,
    /// Reply frame for either direction's request, correlated by `message_id`.
    Reply,
}

/// A message-id'd PPC frame.
///
/// `op` carries the public op name (`plugin.<p>.<op>`) for a request, or a
/// reply/callback sentinel for the return direction. `body` is opaque JSON text
/// so PPC does not parse operation-specific payload schemas.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PpcEnvelope {
    /// Correlates a reply to its request (== the wire `invocation_id`).
    pub message_id: String,
    /// Request or reply. Callbacks are plugin-originated requests on the same
    /// bidirectional channel.
    pub direction: PpcDirection,
    /// Op name for a request; a `"reply"` sentinel for replies.
    pub op: String,
    /// Opaque JSON payload text.
    pub body: String,
}

impl PpcEnvelope {
    /// Frame this envelope as newline-delimited compact JSON via the SAME
    /// [`crate::framing::encode`] the daemon uses (no second wire format). The
    /// `{direction, body}` args object is built by the future port; `message_id`
    /// maps to the envelope `invocation_id` and `op` to the envelope `op`.
    ///
    /// # Errors
    ///
    /// Returns [`PluginError::Ppc`] if shared protocol framing fails.
    pub fn encode(&self) -> Result<Vec<u8>, PluginError> {
        let envelope = self.to_envelope();
        encode(&envelope).map_err(|err| map_protocol(&err))
    }

    /// Decode one framed PPC message produced by [`PpcEnvelope::encode`].
    ///
    /// # Errors
    ///
    /// Returns [`PluginError::Ppc`] if the shared frame is invalid or if it does
    /// not contain a PPC request-shaped payload.
    pub fn decode(bytes: &[u8]) -> Result<Self, PluginError> {
        let envelope = decode(bytes).map_err(|err| map_protocol(&err))?;
        Self::from_envelope(envelope)
    }

    /// Project this frame onto an [`crate::framing::Envelope::Request`], encoding
    /// `op`/`message_id`/`{direction, body}` into the request shape so the shared
    /// framing carries it. Body is opaque JSON text wrapped into the args object.
    fn to_envelope(&self) -> Envelope {
        Envelope::Request(Request {
            op: self.op.clone(),
            invocation_id: self.message_id.clone(),
            args: json!({
                "direction": direction_wire(self.direction),
                "body": self.body,
            }),
        })
    }

    /// Recover a PPC frame from a decoded protocol envelope (the request shape).
    fn from_envelope(envelope: Envelope) -> Result<Self, PluginError> {
        let Envelope::Request(request) = envelope else {
            return Err(PluginError::Ppc(
                "ppc frame must be a request envelope".to_owned(),
            ));
        };
        let direction = request
            .args
            .get("direction")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| PluginError::Ppc("ppc frame missing direction".to_owned()))
            .and_then(parse_direction)?;
        let body = request
            .args
            .get("body")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| PluginError::Ppc("ppc frame missing body".to_owned()))?;
        Ok(Self {
            message_id: request.invocation_id,
            direction,
            op: request.op,
            body: body.to_owned(),
        })
    }
}

const fn direction_wire(direction: PpcDirection) -> &'static str {
    match direction {
        PpcDirection::Request => "request",
        PpcDirection::Reply => "reply",
    }
}

fn parse_direction(raw: &str) -> Result<PpcDirection, PluginError> {
    match raw {
        "request" => Ok(PpcDirection::Request),
        "reply" => Ok(PpcDirection::Reply),
        other => Err(PluginError::Ppc(format!("unknown ppc direction: {other}"))),
    }
}

fn map_protocol(err: &ProtocolError) -> PluginError {
    PluginError::Ppc(err.to_string())
}

#[cfg(test)]
#[path = "../tests/unit/ppc.rs"]
mod tests;
