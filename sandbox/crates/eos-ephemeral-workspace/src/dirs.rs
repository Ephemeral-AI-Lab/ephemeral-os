use std::path::PathBuf;

use crate::EphemeralWorkspaceError;

/// Fresh writable paths allocated for one operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OverlayDirs {
    pub run_dir: PathBuf,
    pub upperdir: PathBuf,
    pub workdir: PathBuf,
}

/// Allocates fresh writable directories for one operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DirAllocator {
    pub writable_root: PathBuf,
}

/// Best-effort cleanup guard for an allocated run directory, for callers that
/// hold dirs without an [`crate::EphemeralWorkspace`] (e.g. plugin overlays).
#[derive(Debug)]
pub struct OverlayDirsGuard {
    path: Option<PathBuf>,
}

impl DirAllocator {
    #[must_use]
    pub fn new(writable_root: PathBuf) -> Self {
        Self { writable_root }
    }

    /// Allocate the per-operation directory set under
    /// `<writable_root>/<kind>/<pid>-<token>/{,upper,work}`.
    ///
    /// # Errors
    ///
    /// Returns [`EphemeralWorkspaceError::DirAllocation`] when directory
    /// creation fails. `kind` and `token` are sanitized into safe path
    /// segments rather than rejected.
    pub fn allocate(
        &self,
        kind: &str,
        token: &str,
    ) -> Result<OverlayDirs, EphemeralWorkspaceError> {
        let kind = sanitized_segment(kind);
        let token = sanitized_segment(token);
        let run_dir = self
            .writable_root
            .join(kind)
            .join(format!("{}-{token}", std::process::id()));
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

        Ok(OverlayDirs {
            run_dir,
            upperdir,
            workdir,
        })
    }
}

impl OverlayDirsGuard {
    #[must_use]
    pub fn new(path: PathBuf) -> Self {
        Self { path: Some(path) }
    }
}

impl Drop for OverlayDirsGuard {
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
