use std::path::{Path, PathBuf};

use crate::EphemeralWorkspaceError;

/// Fresh writable paths allocated for one operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OverlayDirs {
    pub run_dir: PathBuf,
    pub upperdir: PathBuf,
    pub workdir: PathBuf,
}

/// Best-effort cleanup guard for an allocated run directory, for callers that
/// hold dirs without an [`crate::EphemeralWorkspace`] (e.g. plugin overlays).
#[derive(Debug)]
pub struct OverlayDirsGuard(Option<PathBuf>);

/// Allocate daemon/runtime overlay dirs under the configured writable root.
pub fn overlay_run_dirs(
    kind: &str,
    invocation_id: &str,
) -> Result<OverlayDirs, EphemeralWorkspaceError> {
    let writable_root = eos_overlay::overlay_writable_root().map_err(|error| {
        EphemeralWorkspaceError::DirAllocation {
            path: PathBuf::from("overlay_writable_root"),
            reason: error.to_string(),
        }
    })?;
    allocate_overlay_dirs(&writable_root.join("runtime"), kind, invocation_id)
}

pub(crate) fn allocate_overlay_dirs(
    writable_root: &Path,
    kind: &str,
    token: &str,
) -> Result<OverlayDirs, EphemeralWorkspaceError> {
    let run_dir = writable_root.join(sanitized_segment(kind)).join(format!(
        "{}-{}",
        std::process::id(),
        sanitized_segment(token)
    ));
    let upperdir = run_dir.join("upper");
    let workdir = run_dir.join("work");

    for path in [&run_dir, &upperdir, &workdir] {
        std::fs::create_dir_all(path).map_err(|error| EphemeralWorkspaceError::DirAllocation {
            path: path.clone(),
            reason: error.to_string(),
        })?;
    }

    Ok(OverlayDirs {
        run_dir,
        upperdir,
        workdir,
    })
}

impl OverlayDirsGuard {
    #[must_use]
    pub fn new(path: PathBuf) -> Self {
        Self(Some(path))
    }
}

impl Drop for OverlayDirsGuard {
    fn drop(&mut self) {
        if let Some(path) = self.0.take() {
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
