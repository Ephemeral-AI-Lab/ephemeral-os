use std::path::PathBuf;

/// Fresh writable paths allocated for one workspace operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OverlayDirs {
    pub run_dir: PathBuf,
    pub upperdir: PathBuf,
    pub workdir: PathBuf,
}

/// A failed attempt to allocate one overlay scratch path.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("dir allocation failed at {}: {reason}", path.display())]
pub struct DirAllocationError {
    pub path: PathBuf,
    pub reason: String,
}

/// Create standard overlay scratch children under an already chosen run dir.
///
/// # Errors
///
/// Returns [`DirAllocationError`] when `run_dir`, `upper`, or `work` cannot be
/// created.
pub fn create_overlay_dirs(run_dir: PathBuf) -> Result<OverlayDirs, DirAllocationError> {
    let upperdir = run_dir.join("upper");
    let workdir = run_dir.join("work");

    for path in [&run_dir, &upperdir, &workdir] {
        std::fs::create_dir_all(path).map_err(|error| DirAllocationError {
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
