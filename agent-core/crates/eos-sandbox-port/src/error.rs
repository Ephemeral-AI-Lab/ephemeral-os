//! The single library error enum for the host-facing sandbox protocol.
//!
//! Per spec-conventions §8 each crate owns exactly one `thiserror` enum and no
//! `Box<dyn Error>` crosses the public boundary. `SandboxPortError` has two
//! shapes: a `Transport` failure surfaced by a [`SandboxTransport`] implementor
//! (carrying the daemon-resolved error `code` and a user-facing `message`) and a
//! `Decode` failure when a daemon JSON envelope does not match the expected
//! result shape.
//!
//! [`SandboxTransport`]: crate::SandboxTransport

/// Errors raised when calling the sandbox daemon through a [`SandboxTransport`].
///
/// The enum is `#[non_exhaustive]` (errors grow), but its variants are
/// constructible from any crate via [`SandboxPortError::transport`] /
/// [`SandboxPortError::decode`] so the downstream daemon-client implementor
/// (`eos-sandbox-host`) can build them.
///
/// [`SandboxTransport`]: crate::SandboxTransport
#[derive(Debug, Clone, thiserror::Error)]
#[non_exhaustive]
pub enum SandboxPortError {
    /// A sandbox RPC failed at the transport. `code` is the daemon-resolved
    /// structured error code (already normalized by the transport implementor;
    /// see the conflict classifier in `tool_api::parse`), `message` is the
    /// user-facing text. The conflict classifier inspects both.
    #[error("sandbox transport error: {message}")]
    Transport {
        /// Daemon-resolved structured error code, when the daemon supplied one.
        code: Option<String>,
        /// User-facing error message (already stripped of the daemon
        /// `internal_error:` prefix is the caller's job — see
        /// `tool_api::parse::user_visible_error_message`).
        message: String,
    },
    /// A daemon JSON envelope failed to decode into the expected typed result
    /// (e.g. a numeric field carried a bool, which the strict-int decode
    /// rejects).
    #[error("daemon response decode error: {message}")]
    Decode {
        /// Description of the decode mismatch.
        message: String,
    },
}

impl SandboxPortError {
    /// Build a [`SandboxPortError::Transport`] from an optional daemon error code
    /// and a user-facing message. Used by the daemon-client implementor.
    #[must_use]
    pub fn transport(code: Option<String>, message: impl Into<String>) -> Self {
        Self::Transport {
            code,
            message: message.into(),
        }
    }

    /// Build a [`SandboxPortError::Decode`] from a mismatch description.
    #[must_use]
    pub fn decode(message: impl Into<String>) -> Self {
        Self::Decode {
            message: message.into(),
        }
    }

    /// The user-facing message carried by either variant.
    #[must_use]
    pub fn message(&self) -> &str {
        match self {
            Self::Transport { message, .. } | Self::Decode { message } => message,
        }
    }

    /// The daemon-resolved structured error code, present only on a transport
    /// failure that carried one.
    #[must_use]
    pub fn code(&self) -> Option<&str> {
        match self {
            Self::Transport { code, .. } => code.as_deref(),
            Self::Decode { .. } => None,
        }
    }
}
