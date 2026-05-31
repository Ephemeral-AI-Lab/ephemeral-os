//! Checkpoint-based depth control for sandbox layer stacks.
//!
//! Squash is NON-DESTRUCTIVE until the retaining lease releases: it segments
//! the active manifest around the [`crate::lease::LeaseRegistry::lease_head_layers`]
//! barrier set, projects each foldable run into a single checkpoint layer, and
//! pointer-swaps a shorter manifest. Layers below a lease head stay on disk for
//! that lease's frozen reads (see the DUAL-SET note in [`crate::lease`]).
//! `// PORT backend/src/sandbox/layer_stack/squash.py`

use eos_protocol::{LayerRef, Manifest};

use crate::error::LayerStackError;

/// Format string for a freshly-built checkpoint layer id: `B{version:06}-{uuid8}`.
/// The Rust port must reproduce `f"B{next_version:06d}-{uuid4().hex[:8]}"` exactly
/// for layer-id parity.
/// `// PORT backend/src/sandbox/layer_stack/squash.py:179-180 — _default_checkpoint_id`
pub const CHECKPOINT_ID_PREFIX: char = 'B';

/// A foldable run of >=2 contiguous layers that collapse into one checkpoint.
/// `// PORT backend/src/sandbox/layer_stack/squash.py:20-26 — CheckpointSegment`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CheckpointSegment {
    pub layers: Vec<LayerRef>,
}

impl CheckpointSegment {
    /// Construct a segment, enforcing the >=2-layer invariant.
    /// `// PORT backend/src/sandbox/layer_stack/squash.py:24-26 — __post_init__`
    pub fn new(layers: Vec<LayerRef>) -> Result<Self, LayerStackError> {
        if layers.len() <= 1 {
            return Err(LayerStackError::InvalidSquashPlan(
                "checkpoint segments must contain at least two layers".to_owned(),
            ));
        }
        Ok(Self { layers })
    }
}

/// One entry of a squash plan: either a kept single layer or a foldable segment.
/// `// PORT backend/src/sandbox/layer_stack/squash.py:29 — _SquashPlanEntry`
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SquashPlanEntry {
    /// A layer kept as-is (a lease-head barrier or a singleton run).
    Keep(LayerRef),
    /// A run of layers that fold into one checkpoint.
    Segment(CheckpointSegment),
}

/// A computed squash plan: the active manifest snapshot + the per-run entries.
/// Requires >=1 checkpoint segment (else there is nothing to fold).
/// `// PORT backend/src/sandbox/layer_stack/squash.py:32-48 — SquashPlan`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SquashPlan {
    pub active_version: i64,
    pub active_layers: Vec<LayerRef>,
    pub entries: Vec<SquashPlanEntry>,
}

impl SquashPlan {
    /// Construct + validate (non-empty active layers, non-empty entries, >=1
    /// checkpoint segment).
    /// `// PORT backend/src/sandbox/layer_stack/squash.py:38-44 — __post_init__`
    pub fn new(
        active_version: i64,
        active_layers: Vec<LayerRef>,
        entries: Vec<SquashPlanEntry>,
    ) -> Result<Self, LayerStackError> {
        if active_layers.is_empty() {
            return Err(LayerStackError::InvalidSquashPlan(
                "active_layers must not be empty".to_owned(),
            ));
        }
        if entries.is_empty() {
            return Err(LayerStackError::InvalidSquashPlan(
                "entries must not be empty".to_owned(),
            ));
        }
        if !entries
            .iter()
            .any(|e| matches!(e, SquashPlanEntry::Segment(_)))
        {
            return Err(LayerStackError::InvalidSquashPlan(
                "squash plans must include at least one checkpoint segment".to_owned(),
            ));
        }
        Ok(Self {
            active_version,
            active_layers,
            entries,
        })
    }

    /// The checkpoint segments of this plan, in order.
    /// `// PORT backend/src/sandbox/layer_stack/squash.py:46-48 — checkpoint_segments`
    pub fn checkpoint_segments(&self) -> Vec<&CheckpointSegment> {
        self.entries
            .iter()
            .filter_map(|e| match e {
                SquashPlanEntry::Segment(s) => Some(s),
                SquashPlanEntry::Keep(_) => None,
            })
            .collect()
    }
}

/// Plans runs between lease heads and projects each run into a checkpoint layer.
/// `// PORT backend/src/sandbox/layer_stack/squash.py:51-59 — LayerCheckpointSquasher`
#[derive(Debug)]
pub struct LayerCheckpointSquasher {
    _storage_root: std::path::PathBuf,
}

impl LayerCheckpointSquasher {
    /// Bind a squasher to a storage root (owns its own [`crate::MergedView`]).
    /// `// PORT backend/src/sandbox/layer_stack/squash.py:54-59 — __init__`
    pub fn new(storage_root: std::path::PathBuf) -> Self {
        Self {
            _storage_root: storage_root,
        }
    }

    /// Compute a squash plan, or `None` when the manifest is already within
    /// `max_depth` or no run yields >= `min_reduction` folds. Segments around
    /// the `lease_head_layers` barrier set (those layers stay visible).
    /// `// PORT backend/src/sandbox/layer_stack/squash.py:61-93 — plan`
    pub fn plan(
        &self,
        active_manifest: &Manifest,
        max_depth: usize,
        lease_head_layers: &[LayerRef],
        min_reduction: usize,
    ) -> Result<Option<SquashPlan>, LayerStackError> {
        let _ = (active_manifest, max_depth, lease_head_layers, min_reduction);
        // PORT backend/src/sandbox/layer_stack/squash.py:61-93 — _segment_around_lease_heads + depth/min_reduction gates
        todo!("PORT: LayerCheckpointSquasher.plan")
    }

    /// Project a segment's layers into a fresh checkpoint layer directory and
    /// return its `LayerRef` (id format `B{active_version+1:06}-{uuid8}`).
    /// `// PORT backend/src/sandbox/layer_stack/squash.py:95-113 — build_checkpoint`
    pub fn build_checkpoint(
        &self,
        segment: &CheckpointSegment,
        active_version: i64,
    ) -> Result<LayerRef, LayerStackError> {
        let _ = (segment, active_version);
        // PORT backend/src/sandbox/layer_stack/squash.py:95-113 — project(staging) → os.replace(staging, layer_dir)
        todo!("PORT: LayerCheckpointSquasher.build_checkpoint")
    }

    /// Rename a prebuilt checkpoint so its id matches the publishing manifest
    /// version (the `B{manifest_version:06}-…` prefix invariant).
    /// `// PORT backend/src/sandbox/layer_stack/squash.py:115-126 — relabel_checkpoint`
    pub fn relabel_checkpoint(
        &self,
        checkpoint: &LayerRef,
        manifest_version: i64,
    ) -> Result<LayerRef, LayerStackError> {
        let _ = (checkpoint, manifest_version);
        // PORT backend/src/sandbox/layer_stack/squash.py:115-126 — os.replace(current, layer_dir) + fsync parent
        todo!("PORT: LayerCheckpointSquasher.relabel_checkpoint")
    }

    /// Best-effort removal of an uncommitted checkpoint (rollback path).
    /// `// PORT backend/src/sandbox/layer_stack/squash.py:128-130 — discard_checkpoint`
    pub fn discard_checkpoint(&self, checkpoint: &LayerRef) -> Result<(), LayerStackError> {
        let _ = checkpoint;
        // PORT backend/src/sandbox/layer_stack/squash.py:128-130 — rmtree(layer_path, ignore_errors=True)
        todo!("PORT: LayerCheckpointSquasher.discard_checkpoint")
    }
}

/// If the active manifest's tail still equals the plan's snapshotted active
/// layers, return the live prefix above them; else `None` (CAS lost).
/// `// PORT backend/src/sandbox/layer_stack/squash.py:167-176 — manifest_prefix_before_plan`
pub fn manifest_prefix_before_plan<'m>(
    manifest: &'m Manifest,
    plan: &SquashPlan,
) -> Option<&'m [LayerRef]> {
    let _ = (manifest, plan);
    // PORT backend/src/sandbox/layer_stack/squash.py:167-176 — tail-equality check, return manifest.layers[:-planned_depth]
    todo!("PORT: manifest_prefix_before_plan")
}
