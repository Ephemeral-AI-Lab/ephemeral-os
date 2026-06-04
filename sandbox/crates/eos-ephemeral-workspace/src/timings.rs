use std::collections::BTreeMap;
use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Basic resource stats for a captured upperdir tree.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct TreeResourceStats {
    pub files: u64,
    pub dirs: u64,
    pub symlinks: u64,
    pub bytes: u64,
}

/// Timing DTO local to ephemeral workspace policy.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct EphemeralTimings {
    pub lease_acquire_s: Option<f64>,
    pub runner_s: Option<f64>,
    pub capture_s: Option<f64>,
    pub publish_s: Option<f64>,
    pub cleanup_s: Option<f64>,
    pub total_s: f64,
    pub extra: BTreeMap<String, Value>,
}

impl TreeResourceStats {
    #[must_use]
    pub fn collect(path: &Path) -> Self {
        let mut stats = Self::default();
        collect_path(path, &mut stats);
        stats
    }
}

impl EphemeralTimings {
    #[must_use]
    pub fn new(total_s: f64) -> Self {
        Self {
            total_s,
            ..Self::default()
        }
    }

    pub fn insert_extra(&mut self, key: impl Into<String>, value: Value) {
        self.extra.insert(key.into(), value);
    }
}

fn collect_path(path: &Path, stats: &mut TreeResourceStats) {
    let Ok(metadata) = std::fs::symlink_metadata(path) else {
        return;
    };
    let file_type = metadata.file_type();
    if file_type.is_symlink() {
        stats.symlinks = stats.symlinks.saturating_add(1);
        return;
    }
    if file_type.is_file() {
        stats.files = stats.files.saturating_add(1);
        stats.bytes = stats.bytes.saturating_add(metadata.len());
        return;
    }
    if file_type.is_dir() {
        stats.dirs = stats.dirs.saturating_add(1);
        let Ok(entries) = std::fs::read_dir(path) else {
            return;
        };
        for entry in entries.flatten() {
            collect_path(&entry.path(), stats);
        }
    }
}
