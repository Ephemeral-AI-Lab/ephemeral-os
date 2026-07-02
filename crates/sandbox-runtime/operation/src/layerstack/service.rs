mod core;
mod impls;
pub mod model;

pub(crate) use impls::squash::operation_entries as squash_operation_entries;

pub use core::LayerStackService;
pub use model::{
    AmendError, AmendOutcome, LayerStackRevision, ManifestReadWindow, PublishChangesRequest,
    PublishChangesResult,
};
