use thiserror::Error;

use crate::model::CasError;
use crate::stack::publish::model::PublishReject;

#[derive(Debug, Error)]
#[non_exhaustive]
pub enum LayerStackError {
    #[error("active manifest changed: expected version {expected}, found version {found}")]
    ManifestConflict { expected: i64, found: i64 },

    #[error("layer-stack storage root is already owned by another process: {0}")]
    StorageRootOwned(String),

    #[error("layer-stack storage writer lock is closed")]
    StorageWriterLockClosed,

    #[error("invalid lease owner: {0}")]
    InvalidLeaseOwner(String),

    #[error("layer-stack lock poisoned: {0}")]
    LockPoisoned(&'static str),

    #[error("layer-stack publish rejected: {0:?}")]
    PublishRejected(Box<PublishReject>),

    #[error("could not allocate a unique layer id")]
    LayerIdAllocation,

    #[error("manifest error: {0}")]
    Manifest(String),

    #[error("workspace binding error: {0}")]
    WorkspaceBinding(String),

    #[error("file too large: {size} > {limit} bytes")]
    FileTooLarge { size: u64, limit: usize },

    #[error("layer-stack storage error: {0}")]
    Storage(String),

    #[error(transparent)]
    Cas(#[from] CasError),

    #[error("layer-stack io error: {0}")]
    Io(#[from] std::io::Error),
}
