use std::path::PathBuf;

use crate::ephemeral::error::EphemeralWorkspaceError;
use crate::ephemeral::types::{EphemeralRunDirs, InvocationId};

/// Allocates fresh writable directories for one operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EphemeralDirAllocator {
    pub writable_root: PathBuf,
}

/// Best-effort cleanup guard for an allocated run directory.
#[derive(Debug)]
pub struct RunDirCleanup {
    path: Option<PathBuf>,
}

impl EphemeralDirAllocator {
    #[must_use]
    pub fn new(writable_root: PathBuf) -> Self {
        Self { writable_root }
    }

    /// Allocate the per-operation directory set.
    ///
    /// # Errors
    ///
    /// Returns [`EphemeralWorkspaceError`] when the operation kind or invocation
    /// id is unsafe as a path segment, or when directory creation fails.
    pub fn allocate(
        &self,
        kind: &str,
        invocation_id: &InvocationId,
    ) -> Result<EphemeralRunDirs, EphemeralWorkspaceError> {
        let kind = sanitized_segment(kind);
        let invocation = sanitized_segment(&invocation_id.0);
        let run_dir = self
            .writable_root
            .join(kind)
            .join(format!("{}-{invocation}", std::process::id()));
        let upperdir = run_dir.join("upper");
        let workdir = run_dir.join("work");

        for path in [&run_dir, &upperdir, &workdir] {
            std::fs::create_dir_all(path).map_err(|error| {
                EphemeralWorkspaceError::DirAllocation {
                    path: path.clone(),
                    reason: error.to_string(),
                }
            })?;
        }

        Ok(EphemeralRunDirs {
            run_dir,
            upperdir,
            workdir,
        })
    }
}

impl RunDirCleanup {
    #[must_use]
    pub fn new(path: PathBuf) -> Self {
        Self { path: Some(path) }
    }
}

impl Drop for RunDirCleanup {
    fn drop(&mut self) {
        if let Some(path) = self.path.take() {
            let _ = std::fs::remove_dir_all(path);
        }
    }
}

fn sanitized_segment(value: &str) -> String {
    let cleaned: String = value
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.') {
                ch
            } else {
                '_'
            }
        })
        .collect();
    if cleaned.is_empty() {
        "request".to_owned()
    } else {
        cleaned
    }
}
