//! PPC (plugin-process channel): the bidirectional message-id'd request/reply
//! channel that replaces the in-process dynamic loading handler call.
//!
//! # Invariant
//!
//! The PPC reuses the daemon's newline-delimited compact-JSON wire shape — one
//! [`crate::wire::Message`] per message, a single trailing `\n` — over an
//! `AF_UNIX` socket to the daemon-managed service process. It is BIDIRECTIONAL and
//! message-id'd: plugin operation serialization is forbidden, so the daemon
//! multiplexes many in-flight ops over one service connection, and the
//! self-managed mode lets the plugin call BACK to the daemon (the OCC commit
//! callback) on the same channel. The `message_id` correlates a reply to its
//! request and is carried as the wire message's `invocation_id` so the existing
//! [`crate::wire::encode`]/[`crate::wire::decode`] message codec applies
//! unchanged (no second wire format). Callback requests carry typed
//! `parent_message_id` so the daemon can route callback replies while many
//! callback-capable plugin ops are in flight on the same socket.
//!

use crate::wire::{decode, encode, Message, RequestMessage, WireError};
use serde_json::json;

use crate::error::PluginError;

/// Direction of a PPC message on the bidirectional channel.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PpcDirection {
    /// Request message. Daemon -> service invokes a plugin op; service -> daemon
    /// invokes a daemon callback such as self-managed OCC publish.
    Request,
    /// Reply message for either direction's request, correlated by `message_id`.
    Reply,
}

/// A message-id'd PPC message.
///
/// `op` carries the public op name (`plugin.<p>.<op>`) for a request, or a
/// reply/callback sentinel for the return direction. `body` is opaque JSON text
/// so PPC does not parse operation-specific payload schemas.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PpcMessage {
    /// Correlates a reply to its request (== the wire `invocation_id`).
    pub message_id: String,
    /// Callback request owner, when this message is a plugin-originated
    /// callback that must route back to one in-flight daemon request.
    pub parent_message_id: Option<String>,
    /// Request or reply. Callbacks are plugin-originated requests on the same
    /// bidirectional channel.
    pub direction: PpcDirection,
    /// Op name for a request; a `"reply"` sentinel for replies.
    pub op: String,
    /// Opaque JSON payload text.
    pub body: String,
}

impl PpcMessage {
    /// Encode this message as newline-delimited compact JSON via the SAME
    /// [`crate::wire::encode`] the daemon uses (no second wire format). The
    /// `{direction, body}` args object is built by the future port; `message_id`
    /// maps to the wire `invocation_id` and `op` to the wire `op`.
    ///
    /// # Errors
    ///
    /// Returns [`PluginError::Ppc`] if shared wire encoding fails.
    pub fn encode(&self) -> Result<Vec<u8>, PluginError> {
        let message = self.to_wire_message();
        encode(&message).map_err(|err| map_wire_error(&err))
    }

    /// Decode one PPC message produced by [`PpcMessage::encode`].
    ///
    /// # Errors
    ///
    /// Returns [`PluginError::Ppc`] if the shared message is invalid or if it does
    /// not contain a PPC request-shaped payload.
    pub fn decode(bytes: &[u8]) -> Result<Self, PluginError> {
        let message = decode(bytes).map_err(|err| map_wire_error(&err))?;
        Self::from_wire_message(message)
    }

    /// Project this message onto a [`crate::wire::Message::Request`], encoding
    /// `op`/`message_id`/`{direction, body}` into the request shape so the shared
    /// wire message carries it. Body is opaque JSON text wrapped into the args object.
    fn to_wire_message(&self) -> Message {
        Message::Request(RequestMessage {
            op: self.op.clone(),
            invocation_id: self.message_id.clone(),
            args: json!({
                "direction": direction_wire(self.direction),
                "parent_message_id": self.parent_message_id,
                "body": self.body,
            }),
        })
    }

    /// Recover a PPC message from a decoded request-shaped wire message.
    fn from_wire_message(message: Message) -> Result<Self, PluginError> {
        let Message::Request(request) = message else {
            return Err(PluginError::Ppc(
                "ppc message must be a request message".to_owned(),
            ));
        };
        let direction = request
            .args
            .get("direction")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| PluginError::Ppc("ppc message missing direction".to_owned()))
            .and_then(parse_direction)?;
        let body = request
            .args
            .get("body")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| PluginError::Ppc("ppc message missing body".to_owned()))?;
        let parent_message_id = request
            .args
            .get("parent_message_id")
            .and_then(serde_json::Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned);
        Ok(Self {
            message_id: request.invocation_id,
            parent_message_id,
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

fn map_wire_error(err: &WireError) -> PluginError {
    PluginError::Ppc(err.to_string())
}

#[cfg(test)]
#[path = "../tests/unit/ppc.rs"]
mod tests;
