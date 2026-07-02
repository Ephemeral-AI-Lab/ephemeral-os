mod error;
mod service;

pub(crate) use service::squash_operation_entries;

pub use error::LayerStackServiceError;
pub use service::{
    AmendError, AmendOutcome, LayerStackRevision, LayerStackService, ManifestReadWindow,
    PublishChangesRequest, PublishChangesResult,
};
