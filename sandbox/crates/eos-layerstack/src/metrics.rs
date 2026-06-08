//! On-disk storage metrics for a layer-stack root.

use std::path::Path;

use crate::{LayerStack, LayerStackError, LAYERS_DIR, STAGING_DIR};

/// Filesystem storage metrics for a [`LayerStack`] root.
///
/// Counts directory entries under the crate-owned `layers/` and `staging/`
/// layout plus the recursive byte size of every regular file beneath the
/// storage root.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LayerStackStorageMetrics {
    /// Immutable layer directories under `layers/`.
    pub layer_dirs: usize,
    /// In-flight commit/checkpoint staging directories under `staging/`.
    pub staging_dirs: usize,
    /// Recursive byte size of every regular file under the storage root.
    pub storage_bytes: u64,
}

impl LayerStack {
    /// Walk this stack's storage layout for directory counts and total bytes.
    pub fn storage_metrics(&self) -> Result<LayerStackStorageMetrics, LayerStackError> {
        let root = self.storage_root();
        Ok(LayerStackStorageMetrics {
            layer_dirs: count_dirs(&root.join(LAYERS_DIR))?,
            staging_dirs: count_dirs(&root.join(STAGING_DIR))?,
            storage_bytes: storage_bytes(root)?,
        })
    }
}

fn count_dirs(path: &Path) -> Result<usize, LayerStackError> {
    if !path.exists() {
        return Ok(0);
    }
    let mut count = 0;
    for entry in std::fs::read_dir(path)? {
        if entry?.file_type()?.is_dir() {
            count += 1;
        }
    }
    Ok(count)
}

fn storage_bytes(path: &Path) -> Result<u64, LayerStackError> {
    if !path.exists() {
        return Ok(0);
    }
    let mut total = 0;
    let mut stack = vec![path.to_path_buf()];
    while let Some(dir) = stack.pop() {
        for entry in std::fs::read_dir(dir)? {
            let entry = entry?;
            let meta = entry.metadata()?;
            if meta.is_dir() {
                stack.push(entry.path());
            } else if meta.is_file() {
                total += meta.len();
            }
        }
    }
    Ok(total)
}
