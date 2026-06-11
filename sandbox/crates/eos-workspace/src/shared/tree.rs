use std::path::Path;

use eos_overlay::LayerChange;

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

/// Count regular-file bytes in a directory tree.
#[must_use]
pub fn directory_file_bytes(path: &Path) -> u64 {
    TreeResourceStats::collect(path).bytes
}

/// Map captured path changes to their wire `(path, kind)` string pairs.
#[must_use]
pub fn path_changes_to_wire(changes: &[LayerChange]) -> Vec<(String, String)> {
    changes
        .iter()
        .map(|change| (change.path().as_str().to_owned(), change.kind().to_owned()))
        .collect()
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
