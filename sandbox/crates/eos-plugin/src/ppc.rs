//! PPC (plugin-process channel): the bidirectional message-id'd request/reply
//! channel that replaces the in-process importlib handler call.
//!
//! # Invariant
//!
//! The PPC reuses the daemon's newline-delimited compact-JSON framing — one
//! [`eos_protocol::Envelope`] per message, a single trailing `\n` — over an
//! AF_UNIX socket to the warm per-session plugin server. It is BIDIRECTIONAL and
//! message-id'd: the daemon multiplexes many in-flight ops over one warm-server
//! connection, and the self-managed mode lets the plugin call BACK to the daemon
//! (the OCC commit callback) on the same channel. The `message_id` correlates a
//! reply to its request and is carried as the envelope's `invocation_id` so the
//! existing [`eos_protocol::encode`]/[`eos_protocol::decode`] framing applies
//! unchanged (no second wire format, no `serde_json` edge in this crate).
//!
//! `// PORT backend/src/sandbox/ephemeral_workspace/plugin/overlay_child.py:39-69 — JSON payload <-> reply framing`
//! `// PORT backend/src/sandbox/ephemeral_workspace/plugin/overlay_dispatch.py:135-173 — request/output_ref handoff (becomes the PPC channel)`

use eos_protocol::{decode, encode, Envelope, ProtocolError, Request};
use serde_json::json;

use crate::error::PluginError;

/// Direction of a PPC message on the bidirectional channel.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PpcDirection {
    /// Daemon -> warm server: invoke a plugin op.
    Request,
    /// Warm server -> daemon: the op's reply, OR (self-managed) an OCC commit
    /// callback the daemon must service and answer on the same `message_id`.
    Reply,
}

/// A message-id'd PPC frame. `op` carries the public op name (`plugin.<p>.<op>`)
/// for a request, or a reply/callback sentinel for the return direction; `body`
/// is opaque JSON text (the verb args, the tool result, or a callback payload),
/// kept as `String` to avoid pulling `serde_json` onto this crate's dep edge.
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/overlay_child.py:77-99 — _PluginOverlayInvocation payload`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PpcEnvelope {
    /// Correlates a reply to its request (== the wire `invocation_id`).
    pub message_id: String,
    /// `Request` or `Reply` (the bidirectional callback rides the `Reply` lane).
    pub direction: PpcDirection,
    /// Op name for a request; a `"reply"`/`"occ_commit_callback"` sentinel otherwise.
    pub op: String,
    /// Opaque JSON payload text.
    pub body: String,
}

impl PpcEnvelope {
    /// Frame this envelope as newline-delimited compact JSON via the SAME
    /// [`eos_protocol::encode`] the daemon uses (no second wire format). The
    /// `{direction, body}` args object is built by the future port; `message_id`
    /// maps to the envelope `invocation_id` and `op` to the envelope `op`.
    pub fn encode(&self) -> Result<Vec<u8>, PluginError> {
        let envelope = self.to_envelope();
        encode(&envelope).map_err(map_protocol)
    }

    /// Decode one framed PPC message produced by [`PpcEnvelope::encode`].
    pub fn decode(bytes: &[u8]) -> Result<Self, PluginError> {
        let envelope = decode(bytes).map_err(map_protocol)?;
        Self::from_envelope(envelope)
    }

    /// Project this frame onto an [`eos_protocol::Envelope::Request`], encoding
    /// `op`/`message_id`/`{direction, body}` into the request shape so the shared
    /// framing carries it. Body is opaque JSON text wrapped into the args object.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/overlay_dispatch.py:135-158 — payload_ref JSON shape`
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
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/overlay_child.py:81-99 — read args/direction back out`
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

fn direction_wire(direction: PpcDirection) -> &'static str {
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

fn map_protocol(err: ProtocolError) -> PluginError {
    PluginError::Ppc(err.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ppc_envelope_round_trips_through_protocol_framing() {
        let envelope = PpcEnvelope {
            message_id: "msg-1".to_owned(),
            direction: PpcDirection::Request,
            op: "plugin.lsp.hover".to_owned(),
            body: r#"{"path":"main.py"}"#.to_owned(),
        };

        let encoded = envelope.encode().expect("encode ppc envelope");
        assert!(encoded.ends_with(b"\n"));
        let decoded = PpcEnvelope::decode(&encoded).expect("decode ppc envelope");

        assert_eq!(decoded, envelope);
    }

    #[test]
    fn ppc_decode_rejects_non_request_frames() {
        let encoded =
            encode(&Envelope::Response(json!({"success": true}))).expect("encode response frame");

        assert!(matches!(
            PpcEnvelope::decode(&encoded),
            Err(PluginError::Ppc(message)) if message.contains("request envelope")
        ));
    }

    #[test]
    fn ppc_decode_rejects_unknown_direction() {
        let encoded = encode(&Envelope::Request(Request {
            op: "plugin.lsp.hover".to_owned(),
            invocation_id: "msg-1".to_owned(),
            args: json!({"direction": "sideways", "body": "{}"}),
        }))
        .expect("encode request frame");

        assert!(matches!(
            PpcEnvelope::decode(&encoded),
            Err(PluginError::Ppc(message)) if message.contains("unknown ppc direction")
        ));
    }
}
