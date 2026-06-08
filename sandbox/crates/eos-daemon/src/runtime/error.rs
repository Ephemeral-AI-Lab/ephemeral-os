//! Daemon error algebra.
//!
//! `thiserror` enum per crate (no `Box<dyn Error>` in the public API). Source
//! conversions use `#[from]`; messages are lowercase with no trailing
//! punctuation. The lower-crate error types fold in via `#[from]` so a handler
//! can `?`-propagate them; the dispatcher maps a [`DaemonError`] onto the wire
//! [`eos_protocol::ErrorKind`] error envelope.

use thiserror::Error;

/// Failures surfaced by the daemon server, dispatcher, audit ring, and the
/// injected port implementations.
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum DaemonError {
    /// A framed wire message could not be encoded/decoded.
    #[error(transparent)]
    Protocol(#[from] eos_protocol::ProtocolError),

    /// A transport / listener I/O operation failed.
    #[error("daemon io error: {0}")]
    Io(#[from] std::io::Error),

    /// The op named in the request is not registered in the op table.
    #[error("unknown op: {0}")]
    UnknownOp(String),

    /// The envelope was structurally invalid (missing/empty op, non-object args).
    #[error("invalid envelope: {0}")]
    InvalidEnvelope(String),

    /// A request line exceeded [`eos_protocol::MAX_REQUEST_BYTES`].
    #[error("request exceeds {limit} byte limit")]
    RequestTooLarge {
        /// The configured per-request byte ceiling.
        limit: usize,
    },

    /// A TCP request's auth token did not match the configured token.
    #[error("daemon request authentication failed")]
    Unauthorized,

    /// A handler/gate policy refusal (e.g. floor-reset env gate not set).
    #[error("forbidden: {0}")]
    Forbidden(String),

    /// A process-local daemon state mutex was poisoned.
    #[error("daemon state lock poisoned: {0}")]
    StateLockPoisoned(&'static str),

    /// The layer-stack storage / publish layer failed.
    #[error(transparent)]
    LayerStack(#[from] eos_layerstack::LayerStackError),

    /// The OCC publish path failed.
    #[error(transparent)]
    Occ(#[from] eos_occ::OccError),

    /// The daemon-owned overlay pipeline / dispatch failed.
    #[error("overlay pipeline failure: {0}")]
    OverlayPipeline(String),

    /// The plugin (PPC) dispatch failed.
    #[error(transparent)]
    Plugin(#[from] eos_plugin::PluginError),

    /// The isolated-workspace lifecycle failed.
    #[error(transparent)]
    Isolated(#[from] eos_isolated_workspace::IsolatedError),
}

impl DaemonError {
    /// Map this error onto the wire error `kind`.
    ///
    /// The dispatcher uses this to build the structured error envelope; an
    /// otherwise-unclassified handler failure becomes
    /// [`eos_protocol::ErrorKind::InternalError`] with a generated `error_id`.
    #[must_use]
    pub const fn wire_kind(&self) -> eos_protocol::ErrorKind {
        use eos_protocol::ErrorKind;
        match self {
            Self::Protocol(_) => ErrorKind::BadJson,
            Self::UnknownOp(_) => ErrorKind::UnknownOp,
            Self::InvalidEnvelope(_) => ErrorKind::InvalidEnvelope,
            Self::RequestTooLarge { .. } => ErrorKind::RequestTooLarge,
            Self::Unauthorized => ErrorKind::Unauthorized,
            Self::Forbidden(_) => ErrorKind::Forbidden,
            Self::Plugin(eos_plugin::PluginError::ForbiddenInIsolatedWorkspace) => {
                ErrorKind::ForbiddenInIsolatedWorkspace
            }
            _ => ErrorKind::InternalError,
        }
    }
}

impl From<eos_checkpoint_host::CheckpointError> for DaemonError {
    /// Fold a host checkpoint failure onto the matching daemon variant,
    /// preserving variant identity (so `wire_kind` classifies `Forbidden`
    /// correctly) and the original message text.
    fn from(err: eos_checkpoint_host::CheckpointError) -> Self {
        use eos_checkpoint_host::CheckpointError;
        match err {
            CheckpointError::InvalidEnvelope(message) => Self::InvalidEnvelope(message),
            CheckpointError::Forbidden(message) => Self::Forbidden(message),
            CheckpointError::OverlayPipeline(message) => Self::OverlayPipeline(message),
            CheckpointError::LayerStack(source) => Self::LayerStack(source),
            CheckpointError::Io(source) => Self::Io(source),
        }
    }
}

/// Convenience alias for fallible daemon operations.
pub type Result<T> = core::result::Result<T, DaemonError>;
