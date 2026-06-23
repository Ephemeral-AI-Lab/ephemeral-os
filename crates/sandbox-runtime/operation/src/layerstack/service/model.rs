use std::path::PathBuf;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LayerStackRevision {
    pub manifest_version: i64,
    pub root_hash: String,
    pub layer_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PublishChangesRequest {
    pub expected_base: LayerStackRevision,
    pub base_manifest: sandbox_runtime_layerstack::Manifest,
    pub protected_drops: Vec<sandbox_runtime_layerstack::LayerProtectedDrop>,
    pub changes: Vec<sandbox_runtime_layerstack::LayerChange>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PublishChangesResult {
    pub revision: LayerStackRevision,
    pub manifest: sandbox_runtime_layerstack::Manifest,
    pub layer_paths: Vec<PathBuf>,
    pub route_summary: sandbox_runtime_layerstack::PublishRouteSummary,
    pub no_op: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SquashLayerStackResult {
    pub squashed: bool,
    pub revision: Option<LayerStackRevision>,
    pub layer_paths: Vec<PathBuf>,
    pub lease_release_error: Option<String>,
}
