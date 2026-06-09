//! Host-neutral plugin PPC transport and package publish/setup.
//!
//! This `host` module sits between the contract-only `eos-plugin` surface and
//! the daemon: it owns the pieces of the plugin facade that are pure host glue
//! with no OCC / overlay / daemon-state edge:
//!
//! * [`PpcClient`] — the daemon-side PPC request/reply transport over a
//!   connected service socket, including the multiplexing reader thread and the
//!   plugin-originated callback bridge.
//! * package publish/setup ([`ensure_package`], …).
//!
//! The OCC single writer, the per-op overlay, the live process registry, and the
//! OCC callback body stay **daemon-owned**: the daemon injects its callback as a
//! plain `FnMut(PpcEnvelope) -> Result<_, PpcError>` closure, never a second
//! commit queue. This module gains no dependency toward the daemon; the daemon
//! folds [`PpcError`] onto its own `DaemonError`.

mod package;
mod ppc_client;
pub mod ensure_args;
pub mod route;

pub use package::{ensure_package, needs_upload_response, PackageEnsureReport};
pub use ppc_client::{read_frame, PpcClient};

/// Failures surfaced by the plugin PPC transport and package pipeline.
///
/// This is the local error the moved transport/package code raises in place of
/// the daemon's `DaemonError`; the daemon re-maps each variant back onto its own
/// error (preserving the inner [`crate::PluginError`], so the dispatcher
/// still classifies `ForbiddenInIsolatedWorkspace` correctly).
#[derive(Debug, thiserror::Error)]
pub enum PpcError {
    /// A typed plugin-contract failure (PPC framing, ensure, manifest, …).
    #[error(transparent)]
    Plugin(#[from] crate::PluginError),

    /// A PPC envelope could not be framed / parsed.
    #[error(transparent)]
    Protocol(#[from] eos_protocol::ProtocolError),

    /// A socket / filesystem I/O operation failed.
    #[error("plugin ppc io error: {0}")]
    Io(#[from] std::io::Error),

    /// A process-local PPC state mutex was poisoned.
    #[error("daemon state lock poisoned: {0}")]
    LockPoisoned(&'static str),

    /// An injected callback handler failed; carries the handler's message text
    /// verbatim so the daemon's re-map reproduces the original error string.
    #[error("{0}")]
    Callback(String),
}
