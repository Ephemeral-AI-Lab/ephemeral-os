//! Host-neutral plugin runtime glue: PPC transport, package publish/setup,
//! `api.plugin.ensure` parsing, and route/process-spec construction.
//!
//! This crate sits between the contract-only `eos-plugin` surface and the
//! daemon: it owns the pieces of the plugin facade that are pure host glue
//! with no OCC / overlay / daemon-state edge:
//!
//! * [`PpcClient`] — the daemon-side PPC request/reply transport over a
//!   connected service socket, including the multiplexing reader thread and the
//!   plugin-originated callback bridge.
//! * package publish/setup ([`ensure_package`], …).
//! * [`ensure::ParsedEnsure`] — manifest + caller args parsed into operation
//!   routes and service process specs.
//!
//! The OCC single writer, the per-op overlay, the live process registry, and the
//! OCC callback body stay **daemon-owned**: the daemon injects its callback as a
//! plain `FnMut(PpcEnvelope) -> Result<_, PpcError>` closure, never a second
//! commit queue. This crate gains no dependency toward the daemon; the daemon
//! folds [`PpcError`] onto its own `DaemonError`.

#![forbid(unsafe_code)]

pub mod ensure;
mod package;
pub mod route;
mod transport;

pub use package::{ensure_package, needs_upload_response, PackageEnsureReport};
pub use transport::{read_frame, PpcClient};

/// Failures surfaced by the plugin PPC transport and package pipeline.
///
/// This is the local error the transport/package code raises in place of the
/// daemon's `DaemonError`; the daemon re-maps each variant back onto its own
/// error (preserving the inner [`eos_plugin::PluginError`], so the dispatcher
/// still classifies `ForbiddenInIsolatedWorkspace` correctly).
#[derive(Debug, thiserror::Error)]
pub enum PpcError {
    /// A typed plugin-contract failure (PPC framing, ensure, manifest, …).
    #[error(transparent)]
    Plugin(#[from] eos_plugin::PluginError),

    /// A PPC envelope could not be framed / parsed.
    #[error(transparent)]
    Protocol(#[from] eos_plugin::framing::ProtocolError),

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
