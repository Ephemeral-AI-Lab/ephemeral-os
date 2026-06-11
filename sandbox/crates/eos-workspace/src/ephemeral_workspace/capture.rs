use std::path::Path;

use eos_overlay::LayerChange;

use crate::EphemeralWorkspaceError;

/// Basic resource stats for a captured upperdir tree.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct TreeResourceStats {
    pub files: u64,
    pub dirs: u64,
    pub symlinks: u64,
    pub bytes: u64,
}

impl TreeResourceStats {
    #[must_use]
    pub fn collect(path: &Path) -> Self {
        let mut stats = Self::default();
        collect_path(path, &mut stats);
        stats
    }
}

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
/// overlay path); [`crate::EphemeralWorkspace::capture`] wraps it.
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

fn collect_path(path: &Path, stats: &mut TreeResourceStats) {
    let Ok(metadata) = std::fs::symlink_metadata(path) else {
        return;
    };
    let file_type = metadata.file_type();
    if file_type.is_symlink() {
        stats.symlinks = stats.symlinks.saturating_add(1);
    } else if file_type.is_file() {
        stats.files = stats.files.saturating_add(1);
        stats.bytes = stats.bytes.saturating_add(metadata.len());
    } else if file_type.is_dir() {
        stats.dirs = stats.dirs.saturating_add(1);
        if let Ok(entries) = std::fs::read_dir(path) {
            for entry in entries.flatten() {
                collect_path(&entry.path(), stats);
            }
        }
    }
}
