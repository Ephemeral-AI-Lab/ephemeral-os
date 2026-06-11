use std::path::Path;

use eos_overlay::LayerChange;

use super::EphemeralWorkspaceError;
use crate::shared::TreeResourceStats;
/// Captured upperdir changes and resource stats.
#[derive(Debug, Clone, PartialEq)]
pub struct CapturedChanges {
    pub changes: Vec<LayerChange>,
    pub stats: TreeResourceStats,
    pub capture_s: f64,
}

/// Map captured path changes to their wire `(path, kind)` string pairs.
#[must_use]
pub fn path_changes_to_wire(changes: &[LayerChange]) -> Vec<(String, String)> {
    changes
        .iter()
        .map(|change| (change.path().as_str().to_owned(), change.kind().to_owned()))
        .collect()
}

/// Capture an upperdir delta and resource stats.
///
/// Standalone entry for callers that hold raw overlay dirs (the plugin
/// overlay path).
///
/// # Errors
///
/// Returns [`EphemeralWorkspaceError::CaptureFailed`] when the walk fails.
pub fn capture_upperdir(upperdir: &Path) -> Result<CapturedChanges, EphemeralWorkspaceError> {
    let start = std::time::Instant::now();
    let changes = eos_overlay::capture_upperdir(upperdir).map_err(|error| {
        EphemeralWorkspaceError::CaptureFailed {
            reason: error.to_string(),
        }
    })?;
    Ok(CapturedChanges {
        changes,
        stats: TreeResourceStats::collect(upperdir),
        capture_s: start.elapsed().as_secs_f64(),
    })
}
