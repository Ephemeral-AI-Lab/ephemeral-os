use std::path::Path;

use eos_overlay::LayerChange;
use serde::{Deserialize, Serialize};

use crate::stats::TreeResourceStats;
use crate::EphemeralWorkspaceError;

/// Captured upperdir changes and local path classifications.
#[derive(Debug, Clone, PartialEq)]
pub struct CapturedChanges {
    pub changes: Vec<LayerChange>,
    pub path_kinds: Vec<PathChange>,
    pub stats: TreeResourceStats,
    pub capture_s: f64,
}

/// Local path-kind classification for captured upperdir changes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PathChange {
    pub path: String,
    pub kind: PathChangeKind,
}

/// The path operation kind observed in the upperdir.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PathChangeKind {
    Write,
    Delete,
    Symlink,
    OpaqueDir,
}

/// Map captured path changes to their wire `(path, kind)` string pairs.
#[must_use]
pub fn path_changes_to_wire(path_changes: &[PathChange]) -> Vec<(String, String)> {
    path_changes
        .iter()
        .map(|change| {
            (
                change.path.clone(),
                path_change_kind_wire(change.kind).to_owned(),
            )
        })
        .collect()
}

const fn path_change_kind_wire(kind: PathChangeKind) -> &'static str {
    match kind {
        PathChangeKind::Write => "write",
        PathChangeKind::Delete => "delete",
        PathChangeKind::Symlink => "symlink",
        PathChangeKind::OpaqueDir => "opaque_dir",
    }
}

/// Capture an upperdir delta and classify path change kinds.
///
/// Standalone entry for callers that hold raw overlay dirs (the plugin
/// overlay path); [`crate::EphemeralWorkspace::capture`] wraps it.
///
/// # Errors
///
/// Returns [`EphemeralWorkspaceError::CaptureFailed`] when the walk fails.
pub fn capture_upperdir(
    upperdir: &Path,
) -> Result<CapturedChanges, EphemeralWorkspaceError> {
    let start = std::time::Instant::now();
    let changes = eos_overlay::capture_upperdir(upperdir).map_err(|error| {
        EphemeralWorkspaceError::CaptureFailed {
            reason: error.to_string(),
        }
    })?;
    let path_kinds = changes.iter().map(path_change_from_layer_change).collect();
    Ok(CapturedChanges {
        changes,
        path_kinds,
        stats: TreeResourceStats::collect(upperdir),
        capture_s: start.elapsed().as_secs_f64(),
    })
}

fn path_change_from_layer_change(change: &LayerChange) -> PathChange {
    let kind = match change {
        LayerChange::Write { .. } => PathChangeKind::Write,
        LayerChange::Delete { .. } => PathChangeKind::Delete,
        LayerChange::Symlink { .. } => PathChangeKind::Symlink,
        LayerChange::OpaqueDir { .. } => PathChangeKind::OpaqueDir,
    };
    PathChange {
        path: change.path().as_str().to_owned(),
        kind,
    }
}
