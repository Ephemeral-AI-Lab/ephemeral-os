//! Host-neutral plugin PPC transport and package publish/setup.
//!
//! This intermediate host crate sits between the contract-only `eos-plugin`
//! leaf and the daemon: `eos-daemon` → this crate → `eos-plugin`. It owns the
//! pieces of the plugin facade that are pure host glue with no OCC / overlay /
//! daemon-state edge:
//!
//! * [`PpcClient`] — the daemon-side PPC request/reply transport over a
//!   connected service socket, including the multiplexing reader thread and the
//!   plugin-originated callback bridge.
//! * package publish/setup ([`ensure_package`], [`package_roots`], …).
//!
//! The OCC single writer, the per-op overlay, the live process registry, and the
//! OCC callback body stay **daemon-owned**: the daemon injects its callback as a
//! plain `FnMut(PpcEnvelope) -> Result<_, PpcError>` closure, never a second
//! commit queue. This crate gains no dependency toward the daemon; the daemon
//! folds [`PpcError`] onto its own `DaemonError`.
#![forbid(unsafe_code)]

mod package;
mod ppc_router;

pub use package::{
    ensure_package, needs_upload_response, package_roots, PackageEnsureReport, PackageRoots,
};
pub use ppc_router::{read_frame, PpcClient};

/// Failures surfaced by the plugin PPC transport and package pipeline.
///
/// This is the local error the moved transport/package code raises in place of
/// the daemon's `DaemonError`; the daemon re-maps each variant back onto its own
/// error (preserving the inner [`eos_plugin::PluginError`], so the dispatcher
/// still classifies `ForbiddenInIsolatedWorkspace` correctly).
#[derive(Debug, thiserror::Error)]
pub enum PpcError {
    /// A typed plugin-contract failure (PPC framing, ensure, manifest, …).
    #[error(transparent)]
    Plugin(#[from] eos_plugin::PluginError),

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
