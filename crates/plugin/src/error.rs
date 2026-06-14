//! Error type for plugin registration, manifests, service state, and PPC wire messages.

use thiserror::Error;

/// Failures surfaced by plugin contracts and the PPC channel.
///
/// The Rust side raises bare `PluginOpRegistrationError` / `PluginOpConflictError`
/// / `PluginEnsureError` / `RuntimeError` at the same boundaries; this enum
/// reproduces those failure classes as a typed surface the daemon translates into
/// the wire error response. Concrete overlay/OCC failures are daemon-owned.
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum PluginError {
    /// `register_plugin_op` got an invalid plugin name or empty op name, or was
    /// invoked from outside the `plugins.catalog.<plugin>.*` namespace, or with
    /// `PluginOperationIntent::Lifecycle` (reserved for sandbox lifecycle ops, not plugin tools).
    #[error("plugin op registration error: {0}")]
    Registration(String),

    /// Two distinct handlers tried to register the same `(plugin, op)` pair.
    #[error("plugin op {0} already has a different handler registered")]
    Conflict(String),

    /// `sandbox.plugin.ensure` failed to load or warm a plugin runtime, or the
    /// `layer_stack_root` was missing / mismatched against the workspace binding.
    #[error("plugin ensure failed: {0}")]
    Ensure(String),

    /// A plugin/service manifest or service key was malformed.
    #[error("plugin manifest error: {0}")]
    Manifest(String),

    /// A read-only plugin service tried to answer from an old projection.
    #[error("plugin projection stale: {0}")]
    ProjectionStale(String),

    /// A PPC message could not be encoded/parsed, or the service process reply
    /// carried an unknown / unmatched message id.
    #[error("ppc channel error: {0}")]
    Ppc(String),

    /// Plugin dispatch was refused because an isolated workspace is active for
    /// this agent (maps to `ErrorKind::ForbiddenInIsolatedWorkspace`). Plugin /
    /// LSP operations are blocked while isolated mode is active.
    #[error("plugin operations are forbidden while an isolated workspace is active")]
    ForbiddenInIsolatedWorkspace,
}

/// Convenience alias for fallible plugin operations.
pub type Result<T> = core::result::Result<T, PluginError>;
