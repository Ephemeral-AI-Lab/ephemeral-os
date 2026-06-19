use std::collections::BTreeSet;

use crate::error::LayerStackError;
use crate::model::{LayerRef, Manifest};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LeaseAwareCheckpointMode {
    View,
    DeltaRequired,
}

impl LeaseAwareCheckpointMode {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::View => "view",
            Self::DeltaRequired => "delta_required",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReclaimingInterval {
    pub layers: Vec<LayerRef>,
    pub checkpoint_mode: LeaseAwareCheckpointMode,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LeaseAwarePlanEntry {
    KeepProtected(LayerRef),
    KeepUnleased(LayerRef),
    ReclaimingInterval(ReclaimingInterval),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeaseAwarePlan {
    pub active_version: i64,
    pub active_layer_count: usize,
    pub protected_layer_count: usize,
    pub kept_unleased_layer_count: usize,
    pub reclaiming_interval_count: usize,
    pub reclaiming_layer_count: usize,
    pub entries: Vec<LeaseAwarePlanEntry>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeaseAwareReclaimOutcome {
    pub manifest: Option<Manifest>,
    pub protected_layer_count: usize,
    pub planned_reclaiming_interval_count: usize,
    pub view_checkpoint_count: usize,
    pub delta_checkpoint_count: usize,
    pub skipped_delta_interval_count: usize,
    pub removed_layer_count: usize,
    pub active_depth_before: usize,
    pub active_depth_after: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeaseAwareCopyThroughOutcome {
    pub manifest: Option<Manifest>,
    pub protected_layer_count: usize,
    pub checkpoint_count: usize,
    pub removed_layer_count: usize,
    pub bytes_added: u64,
    pub protected_pinned_bytes: u64,
    pub active_depth_before: usize,
    pub active_depth_after: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeaseParentCompactionOutcome {
    pub lease_manifest: Option<Manifest>,
    pub active_manifest: Option<Manifest>,
    pub compact_parent_layer: Option<LayerRef>,
    pub compacted_parent_layer_count: usize,
    pub removed_layer_count: usize,
    pub bytes_added: u64,
    pub lease_depth_before: usize,
    pub lease_depth_after: usize,
    pub active_depth_before: usize,
    pub active_depth_after: usize,
}

impl LeaseAwarePlan {
    #[must_use]
    pub fn active_depth_after_reclaiming_checkpoints(&self) -> usize {
        self.entries.len()
    }

    #[must_use]
    pub fn has_reclaiming_intervals(&self) -> bool {
        self.reclaiming_interval_count > 0
    }

    pub fn reclaiming_intervals(&self) -> impl Iterator<Item = &ReclaimingInterval> {
        self.entries.iter().filter_map(|entry| match entry {
            LeaseAwarePlanEntry::ReclaimingInterval(interval) => Some(interval),
            LeaseAwarePlanEntry::KeepProtected(_) | LeaseAwarePlanEntry::KeepUnleased(_) => None,
        })
    }
}

pub fn plan_lease_aware_gaps(
    active_manifest: &Manifest,
    protected_layers: &[LayerRef],
    min_reclaiming_interval_layers: usize,
) -> Result<LeaseAwarePlan, LayerStackError> {
    if min_reclaiming_interval_layers == 0 {
        return Err(LayerStackError::InvalidSquashPlan(
            "min_reclaiming_interval_layers must be positive".to_owned(),
        ));
    }

    let protected = protected_layers.iter().collect::<BTreeSet<_>>();
    let active_protected = active_manifest
        .layers
        .iter()
        .filter(|layer| protected.contains(layer))
        .count();
    let mut entries = Vec::new();
    let mut run = Vec::new();
    let mut kept_unleased_layer_count = 0;
    let mut reclaiming_interval_count = 0;
    let mut reclaiming_layer_count = 0;

    for layer in &active_manifest.layers {
        if protected.contains(layer) {
            flush_unleased_run(
                &mut entries,
                &mut run,
                min_reclaiming_interval_layers,
                true,
                &mut kept_unleased_layer_count,
                &mut reclaiming_interval_count,
                &mut reclaiming_layer_count,
            );
            entries.push(LeaseAwarePlanEntry::KeepProtected(layer.clone()));
        } else {
            run.push(layer.clone());
        }
    }
    flush_unleased_run(
        &mut entries,
        &mut run,
        min_reclaiming_interval_layers,
        false,
        &mut kept_unleased_layer_count,
        &mut reclaiming_interval_count,
        &mut reclaiming_layer_count,
    );

    Ok(LeaseAwarePlan {
        active_version: active_manifest.version,
        active_layer_count: active_manifest.layers.len(),
        protected_layer_count: active_protected,
        kept_unleased_layer_count,
        reclaiming_interval_count,
        reclaiming_layer_count,
        entries,
    })
}

fn flush_unleased_run(
    entries: &mut Vec<LeaseAwarePlanEntry>,
    run: &mut Vec<LayerRef>,
    min_reclaiming_interval_layers: usize,
    has_kept_lower_layer: bool,
    kept_unleased_layer_count: &mut usize,
    reclaiming_interval_count: &mut usize,
    reclaiming_layer_count: &mut usize,
) {
    if run.is_empty() {
        return;
    }
    if run.len() < min_reclaiming_interval_layers {
        *kept_unleased_layer_count += run.len();
        entries.extend(run.drain(..).map(LeaseAwarePlanEntry::KeepUnleased));
        return;
    }

    let layers = std::mem::take(run);
    *reclaiming_interval_count += 1;
    *reclaiming_layer_count += layers.len();
    entries.push(LeaseAwarePlanEntry::ReclaimingInterval(
        ReclaimingInterval {
            layers,
            checkpoint_mode: if has_kept_lower_layer {
                LeaseAwareCheckpointMode::DeltaRequired
            } else {
                LeaseAwareCheckpointMode::View
            },
        },
    ));
}
