//! OCC commit staging directories owned by layer-stack storage.
//!
//! Staging lives under `storage_root/staging`; an OCC publish stages its layer
//! bytes here before the atomic pointer-swap into `layers/`.
//! `// PORT backend/src/sandbox/layer_stack/commit_staging.py`

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use crate::error::LayerStackError;
use crate::STAGING_DIR;

/// A handle to one allocated OCC staging directory.
/// `// PORT backend/src/sandbox/layer_stack/commit_staging.py:13-16 — CommitStagingArea`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommitStagingArea {
    /// Directory basename (the staging id callers later pass to `drop`).
    pub staging_id: String,
    /// Absolute path to the staging directory.
    pub path: PathBuf,
}

/// Allocate a fresh staging directory under `storage_root/staging`.
///
/// The directory basename is prefixed by a filesystem-safe slice of
/// `request_id`.
///
/// # Errors
///
/// Returns [`LayerStackError`] when the staging root cannot be created or a
/// unique staging directory cannot be allocated.
/// `// PORT backend/src/sandbox/layer_stack/commit_staging.py:19-31 — allocate_commit_staging`
pub fn allocate_commit_staging(
    storage_root: &Path,
    request_id: &str,
) -> Result<CommitStagingArea, LayerStackError> {
    let staging_root = storage_root.join(STAGING_DIR);
    std::fs::create_dir_all(&staging_root)?;
    let safe = safe_prefix(request_id);
    for _ in 0..100 {
        let id = format!(
            "occ-commit-{safe}-{}-{}",
            std::process::id(),
            NEXT_STAGING.fetch_add(1, Ordering::Relaxed)
        );
        let path = staging_root.join(&id);
        match std::fs::create_dir(&path) {
            Ok(()) => {
                return Ok(CommitStagingArea {
                    staging_id: id,
                    path,
                });
            }
            Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(err) => return Err(err.into()),
        }
    }
    Err(LayerStackError::Storage(
        "could not allocate a unique commit staging directory".to_owned(),
    ))
}

/// Remove a previously-allocated staging directory by id (best-effort rmtree).
///
/// # Errors
///
/// Returns [`LayerStackError`] when `staging_id` is malformed or when removing
/// the staging directory fails for any reason other than not found.
/// `// PORT backend/src/sandbox/layer_stack/commit_staging.py:34-37 — drop_commit_staging`
pub fn drop_commit_staging(storage_root: &Path, staging_id: &str) -> Result<(), LayerStackError> {
    if staging_id.is_empty()
        || staging_id.contains('/')
        || staging_id.contains('\\')
        || staging_id.contains('\0')
        || staging_id == "."
        || staging_id == ".."
    {
        return Err(LayerStackError::Storage(format!(
            "invalid staging id: {staging_id:?}"
        )));
    }
    let path = storage_root.join(STAGING_DIR).join(staging_id);
    match std::fs::remove_dir_all(path) {
        Ok(()) => Ok(()),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(err) => Err(err.into()),
    }
}

static NEXT_STAGING: AtomicU64 = AtomicU64::new(0);

fn safe_prefix(request_id: &str) -> String {
    let safe: String = request_id
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric() || *ch == '-' || *ch == '_')
        .take(32)
        .collect();
    if safe.is_empty() {
        "unknown".to_owned()
    } else {
        safe
    }
}
