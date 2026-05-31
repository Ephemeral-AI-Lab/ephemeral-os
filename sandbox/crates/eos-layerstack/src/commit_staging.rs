//! OCC commit staging directories owned by layer-stack storage.
//!
//! Staging lives under `storage_root/staging`; an OCC publish stages its layer
//! bytes here before the atomic pointer-swap into `layers/`.
//! `// PORT backend/src/sandbox/layer_stack/commit_staging.py`

use std::path::{Path, PathBuf};

use crate::error::LayerStackError;

/// A handle to one allocated OCC staging directory.
/// `// PORT backend/src/sandbox/layer_stack/commit_staging.py:13-16 — CommitStagingArea`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommitStagingArea {
    /// Directory basename (the staging id callers later pass to `drop`).
    pub staging_id: String,
    /// Absolute path to the staging directory.
    pub path: PathBuf,
}

/// Allocate a fresh staging directory under `storage_root/staging`, prefixed by
/// a filesystem-safe slice of `request_id`.
/// `// PORT backend/src/sandbox/layer_stack/commit_staging.py:19-31 — allocate_commit_staging`
pub fn allocate_commit_staging(
    storage_root: &Path,
    request_id: &str,
) -> Result<CommitStagingArea, LayerStackError> {
    let _ = (storage_root, request_id);
    // PORT backend/src/sandbox/layer_stack/commit_staging.py:19-31 — mkdtemp(prefix="occ-commit-<safe>-", dir=staging)
    todo!("PORT: allocate_commit_staging")
}

/// Remove a previously-allocated staging directory by id (best-effort rmtree).
/// `// PORT backend/src/sandbox/layer_stack/commit_staging.py:34-37 — drop_commit_staging`
pub fn drop_commit_staging(storage_root: &Path, staging_id: &str) -> Result<(), LayerStackError> {
    let _ = (storage_root, staging_id);
    // PORT backend/src/sandbox/layer_stack/commit_staging.py:34-37 — rmtree(storage_root/staging/staging_id, ignore_errors=True)
    todo!("PORT: drop_commit_staging")
}
