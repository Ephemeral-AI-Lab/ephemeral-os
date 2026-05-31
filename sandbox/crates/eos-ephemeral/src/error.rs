//! Error type for the per-operation ephemeral pipeline.

use thiserror::Error;

/// Failures surfaced by the ephemeral pipeline, dispatch, and registry.
///
/// The Python side raises bare `RuntimeError`/`ValueError`/`WorkspaceBindingError`
/// at the same boundaries; this enum reproduces those failure classes as a typed
/// surface the daemon can translate into the wire error envelope.
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum EphemeralError {
    /// A pipeline operation needs a bound layer stack but none was attached.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:133 — "requires layer_stack"`
    #[error("ephemeral pipeline requires a layer stack")]
    MissingLayerStack,

    /// The requested workspace_root does not match the persisted binding.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline_registry.py:50-54 — WorkspaceBindingError`
    #[error("workspace binding mismatch: {0}")]
    WorkspaceBinding(String),

    /// A request argument was missing or malformed (single-path contract,
    /// required `layer_stack_root`, malformed `edits`).
    /// `// PORT backend/src/sandbox/daemon/workspace_tool/payloads.py:39-62 — require_* validators`
    #[error("invalid request argument: {0}")]
    InvalidArgument(String),

    /// `exit_isolated_workspace` is draining for this agent; the dispatch must
    /// be retried after the drain completes.
    /// `// PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:48-61 — LifecycleInProgressError`
    #[error("lifecycle in progress for agent {0}; retry after exit completes")]
    LifecycleInProgress(String),

    /// The overlay mount / capture / publish cycle failed.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:370-400 — _mount_active / mount_overlay`
    #[error("overlay pipeline failure: {0}")]
    Overlay(String),
}

/// Convenience alias for fallible ephemeral operations.
pub type Result<T> = core::result::Result<T, EphemeralError>;
