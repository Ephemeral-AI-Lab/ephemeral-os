//! Error type for shared ephemeral-workspace contracts.

use thiserror::Error;

/// Failures surfaced across the shared-workspace contract boundary.
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum EphemeralError {
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
