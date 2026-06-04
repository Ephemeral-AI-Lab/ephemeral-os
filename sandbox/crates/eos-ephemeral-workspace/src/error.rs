use std::path::PathBuf;

use thiserror::Error;

/// Errors raised by fresh per-operation workspace policy.
#[derive(Debug, Error)]
pub enum EphemeralWorkspaceError {
    /// A caller supplied an invalid workspace, invocation, or operation value.
    #[error("invalid argument: {0}")]
    InvalidArgument(String),
    /// Snapshot acquisition failed before a run could start.
    #[error("snapshot acquire failed: {reason}")]
    SnapshotAcquire { reason: String },
    /// Lease release failed during cleanup.
    #[error("lease release failed for {lease_id}: {reason}")]
    LeaseRelease { lease_id: String, reason: String },
    /// Fresh writable directory allocation failed.
    #[error("dir allocation failed at {}: {reason}", path.display())]
    DirAllocation { path: PathBuf, reason: String },
    /// The namespace runner failed before returning a runner result.
    #[error("runner failed: {reason}")]
    RunnerFailed { reason: String },
    /// Upperdir capture failed.
    #[error("capture failed: {reason}")]
    CaptureFailed { reason: String },
    /// Publishing captured changes failed.
    #[error("publish failed: {reason}")]
    PublishFailed { reason: String },
    /// Best-effort cleanup failed.
    #[error("cleanup failed at {}: {reason}", path.display())]
    CleanupFailed { path: PathBuf, reason: String },
    /// I/O failed at a local filesystem edge.
    #[error("{context}: {source}")]
    Io {
        context: String,
        #[source]
        source: std::io::Error,
    },
    /// JSON parsing or serialization failed.
    #[error("{context}: {source}")]
    Serde {
        context: String,
        #[source]
        source: serde_json::Error,
    },
}
