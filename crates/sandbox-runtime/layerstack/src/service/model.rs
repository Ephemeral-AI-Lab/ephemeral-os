use std::path::PathBuf;

use crate::LayerRef;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Snapshot {
    pub manifest_version: i64,
    pub root_hash: String,
    pub layer_paths: Vec<PathBuf>,
}

/// Live lease state of a single active-manifest layer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LayerStatus {
    pub layer: LayerRef,
    pub leased_by_workspaces: usize,
}

/// Per-layer breakdown of the active manifest, computed from the live leases.
///
/// `layers` is ordered newest → base; the booked-by relation is a pure function
/// of this order plus `leased_by_workspaces`, so it is derived at render rather
/// than stored.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StackObservation {
    pub manifest_version: i64,
    pub root_hash: String,
    pub active_lease_count: usize,
    pub layers: Vec<LayerStatus>,
}
