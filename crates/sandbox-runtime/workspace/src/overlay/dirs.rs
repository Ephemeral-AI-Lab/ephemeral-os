use std::path::PathBuf;

/// Fresh writable paths allocated for one workspace operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OverlayDirs {
    pub run_dir: PathBuf,
    pub upperdir: PathBuf,
    pub workdir: PathBuf,
}

/// Create standard overlay scratch children under an already chosen run dir.
///
/// # Errors
///
/// Returns an I/O error when `run_dir`, `upper`, or `work` cannot be created.
pub fn create_overlay_dirs(run_dir: PathBuf) -> std::io::Result<OverlayDirs> {
    let upperdir = run_dir.join("upper");
    let workdir = run_dir.join("work");

    for path in [&run_dir, &upperdir, &workdir] {
        std::fs::create_dir_all(path)?;
    }

    Ok(OverlayDirs {
        run_dir,
        upperdir,
        workdir,
    })
}
