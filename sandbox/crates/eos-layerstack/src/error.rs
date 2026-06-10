//! Layer-stack error algebra.

use thiserror::Error;

use crate::model::CasError;

/// Errors raised by the durable layer-stack storage layer.
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum LayerStackError {
    /// The active manifest changed under a publish/squash transaction (CAS lost).
    #[error("active manifest changed: expected version {expected}, found version {found}")]
    ManifestConflict { expected: i64, found: i64 },

    /// The storage root is already owned by another daemon process (flock held).
    #[error("layer-stack storage root is already owned by another process: {0}")]
    StorageRootOwned(String),

    /// The storage-writer lock lease has been closed.
    #[error("layer-stack storage writer lock is closed")]
    StorageWriterLockClosed,

    /// A caller supplied an invalid snapshot lease owner.
    #[error("invalid lease owner: {0}")]
    InvalidLeaseOwner(String),

    /// A process-local storage lock was poisoned by a panic in another holder.
    #[error("layer-stack lock poisoned: {0}")]
    LockPoisoned(&'static str),

    /// A squash/checkpoint plan invariant was violated (e.g. <2-layer segment).
    #[error("invalid squash plan: {0}")]
    InvalidSquashPlan(String),

    /// Could not allocate a unique layer id within the attempt budget.
    #[error("could not allocate a unique layer id")]
    LayerIdAllocation,

    /// The active manifest could not be parsed or violated storage invariants.
    #[error("manifest error: {0}")]
    Manifest(String),

    /// The layer-stack workspace binding is missing or invalid.
    #[error("workspace binding error: {0}")]
    WorkspaceBinding(String),

    /// A manifest-referenced layer no longer contains the requested data.
    #[error("layer-stack storage error: {0}")]
    Storage(String),

    /// A CAS path / manifest value failed to parse or validate.
    #[error(transparent)]
    Cas(#[from] CasError),

    /// An underlying filesystem operation failed.
    #[error("layer-stack io error: {0}")]
    Io(#[from] std::io::Error),
}
