//! Error type for plugin registration, PPC framing, and the three dispatch modes.

use thiserror::Error;

/// Failures surfaced by the plugin op registry, the PPC channel, the warm-server
/// registry, and the three dispatch modes.
///
/// The Python side raises bare `PluginOpRegistrationError` / `PluginOpConflictError`
/// / `PluginEnsureError` / `RuntimeError` at the same boundaries; this enum
/// reproduces those failure classes as a typed surface the daemon translates into
/// the wire error envelope. The OCC-publish failures from the WRITE_ALLOWED and
/// self-managed paths flow up through [`EphemeralError`](eos_ephemeral::EphemeralError)
/// (the SAME single-writer port — no second writer, MF-1).
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum PluginError {
    /// `register_plugin_op` got an invalid plugin name or empty op name, or was
    /// invoked from outside the `plugins.catalog.<plugin>.*` namespace, or with
    /// `Intent::Lifecycle` (reserved for sandbox lifecycle ops, not plugin tools).
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:109-121 — registration validation`
    #[error("plugin op registration error: {0}")]
    Registration(String),

    /// Two distinct handlers tried to register the same `(plugin, op)` pair.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:129-132 — PluginOpConflictError`
    #[error("plugin op {0} already has a different handler registered")]
    Conflict(String),

    /// `api.plugin.ensure` failed to load or warm a plugin runtime, or the
    /// `layer_stack_root` was missing / mismatched against the workspace binding.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/runtime_api.py:55-56,331-344 — PluginEnsureError`
    #[error("plugin ensure failed: {0}")]
    Ensure(String),

    /// A plugin/service manifest or service key was malformed.
    #[error("plugin manifest error: {0}")]
    Manifest(String),

    /// A read-only plugin service tried to answer from an old projection.
    #[error("plugin projection stale: {0}")]
    ProjectionStale(String),

    /// A PPC envelope could not be framed/parsed, or the warm server's reply
    /// carried an unknown / unmatched message id.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/overlay_child.py:39-45 — payload decode`
    #[error("ppc channel error: {0}")]
    Ppc(String),

    /// Plugin dispatch was refused because an isolated workspace is active for
    /// this agent (maps to `ErrorKind::ForbiddenInIsolatedWorkspace`). Plugin /
    /// LSP operations are blocked while isolated mode is active.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:14-23 — isolated-mode blocks plugin ops`
    #[error("plugin operations are forbidden while an isolated workspace is active")]
    ForbiddenInIsolatedWorkspace,

    /// The WRITE_ALLOWED / self-managed overlay+OCC publish cycle failed. Carries
    /// the underlying single-writer error so the failure provenance (the ONE
    /// `occ-commit-queue` writer per `layer_stack_root`) is preserved.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/overlay_dispatch.py:197-203 — plugin_overlay_publish_failed`
    #[error("plugin overlay publish failed: {0}")]
    Overlay(#[from] eos_ephemeral::EphemeralError),

    /// A read/snapshot through the layer-stack HINGE failed (projection path).
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/projection.py:97-101 — active_manifest_key`
    #[error("layer stack projection failed: {0}")]
    Projection(#[from] eos_layerstack::LayerStackError),
}

/// Convenience alias for fallible plugin operations.
pub type Result<T> = core::result::Result<T, PluginError>;
