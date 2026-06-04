use std::path::Path;

use eos_protocol::LayerChange;

use crate::error::EphemeralWorkspaceError;
use crate::timings::TreeResourceStats;
use crate::types::{PathChange, PathChangeKind};

/// Captured publishable upperdir changes and local path classifications.
#[derive(Debug, Clone, PartialEq)]
pub struct CapturedUpperdir {
    pub changes: Vec<LayerChange>,
    pub path_kinds: Vec<PathChange>,
    pub stats: TreeResourceStats,
    pub capture_s: f64,
}

/// Capture an ephemeral upperdir and classify path change kinds.
///
/// # Errors
///
/// Returns [`EphemeralWorkspaceError`] when overlay capture fails.
pub fn capture_for_publish(upperdir: &Path) -> Result<CapturedUpperdir, EphemeralWorkspaceError> {
    let start = std::time::Instant::now();
    let changes = eos_overlay::capture_upperdir(upperdir).map_err(|error| {
        EphemeralWorkspaceError::CaptureFailed {
            reason: error.to_string(),
        }
    })?;
    let path_kinds = changes.iter().map(path_change_from_layer_change).collect();
    Ok(CapturedUpperdir {
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
