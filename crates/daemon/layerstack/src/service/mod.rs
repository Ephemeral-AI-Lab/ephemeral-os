pub(crate) mod cache;
mod impls;
mod model;
pub(crate) mod support;

pub use impls::{
    acquire_snapshot_with_lease, compact_snapshot_layers, get_snapshot,
    publish_changes_to_layerstack, release_lease,
};
pub use model::{
    CompactSnapshotLayersRequest, CompactSnapshotLayersResult, LeasedSnapshot,
    PublishChangesRequest, Snapshot,
};

#[doc(hidden)]
pub(crate) use cache::reset_service_cache_for_tests;
