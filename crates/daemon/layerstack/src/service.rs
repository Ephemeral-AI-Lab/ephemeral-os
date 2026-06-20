#[path = "service/cache.rs"]
pub(crate) mod cache;
#[path = "service/impls/mod.rs"]
mod impls;
#[path = "service/model.rs"]
mod model;
#[path = "service/support.rs"]
pub(crate) mod support;

pub use impls::{
    acquire_snapshot_with_lease, compact_snapshot_layers, get_snapshot,
    publish_changes_to_layerstack, release_lease,
};
pub use model::{
    CompactSnapshotLayersRequest, CompactSnapshotLayersResult, LeasedSnapshot,
    PublishChangesRequest, PublishChangesResult, Snapshot,
};

#[doc(hidden)]
pub(crate) use cache::reset_service_cache_for_tests;
