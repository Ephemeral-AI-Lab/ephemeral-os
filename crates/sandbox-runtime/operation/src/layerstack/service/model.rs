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
    /// Opaque owner string this publish stamps onto its `Command` lines
    /// (`workspace_session:<id>` when a workspace was mounted, else
    /// `operation:<id>`). Not passed to layerstack — mapped to audit events
    /// above it, after the layer commits.
    pub owner: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PublishChangesResult {
    pub revision: LayerStackRevision,
    pub manifest: sandbox_runtime_layerstack::Manifest,
    pub layer_paths: Vec<PathBuf>,
    pub route_summary: sandbox_runtime_layerstack::PublishRouteSummary,
    pub no_op: bool,
}
