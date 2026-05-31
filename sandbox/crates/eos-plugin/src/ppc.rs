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

use eos_protocol::{decode, encode, Envelope, ProtocolError};

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
        // PORT overlay_dispatch.py:135-158 — build {op, invocation_id=message_id,
        //   args:{direction, body}} so eos_protocol::encode frames it unchanged.
        let _ = (&self.message_id, &self.direction, &self.op, &self.body);
        todo!("PORT overlay_dispatch.py:135-158 — map PpcEnvelope -> eos_protocol::Request{{op, invocation_id, args:{{direction, body}}}}")
    }

    /// Recover a PPC frame from a decoded protocol envelope (the request shape).
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/overlay_child.py:81-99 — read args/direction back out`
    fn from_envelope(envelope: Envelope) -> Result<Self, PluginError> {
        // PORT overlay_child.py:81-99 — read op/invocation_id/args{direction, body}
        //   back off the request envelope; reject error/bare-response frames.
        let _ = envelope;
        todo!("PORT overlay_child.py:81-99 — map eos_protocol::Envelope::Request -> PpcEnvelope")
    }
}

fn map_protocol(err: ProtocolError) -> PluginError {
    PluginError::Ppc(err.to_string())
}
