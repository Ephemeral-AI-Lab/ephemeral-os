//! Writable overlay directory allocation.
//!
//! Overlayfs needs a writable `upperdir` plus a sibling `workdir` for every
//! mounted overlay. Lower layers are leased from the layer stack; this module
//! owns only the upper/work side of the mount. There is intentionally NO
//! fallback root — Docker-backed sandboxes provide the writable filesystem at
//! `/eos-mount-scratch`.

use std::path::{Path, PathBuf};

use crate::error::Result;

/// Canonical filesystem for overlay `upperdir`/`workdir`.
/// `// PORT backend/src/sandbox/overlay/writable_dirs.py:13 — OVERLAY_WRITABLE_ROOT`
pub const OVERLAY_WRITABLE_ROOT: &str = "/eos-mount-scratch/eos-sandbox-runtime";

/// Per-overlay writable directories created beside each other under one run dir.
/// `// PORT backend/src/sandbox/overlay/writable_dirs.py:20-26 — OverlayWritableDirs`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OverlayWritableDirs {
    /// The per-overlay run directory the upper/work dirs live under.
    pub run_dir: PathBuf,
    /// The overlay `upperdir` (`run_dir/upper`).
    pub upperdir: PathBuf,
    /// The overlay `workdir` (`run_dir/work`).
    pub workdir: PathBuf,
}

/// Return the canonical writable root, creating it if its parent exists.
///
/// Mirrors Python `overlay_writable_root()`: create `OVERLAY_WRITABLE_ROOT`
/// only when its parent is already a directory, then require the result to be a
/// directory or raise [`OverlayError::WritableRootUnavailable`]. No fallback.
/// `// PORT backend/src/sandbox/overlay/writable_dirs.py:29-43 — overlay_writable_root`
pub fn overlay_writable_root() -> Result<PathBuf> {
    // PORT backend/src/sandbox/overlay/writable_dirs.py:36-42 — mkdir-if-parent then is_dir gate
    todo!()
}

/// Create and return the `upper`/`work` dirs for one overlay instance.
/// `// PORT backend/src/sandbox/overlay/writable_dirs.py:46-52 — allocate_overlay_writable_dirs`
pub fn allocate_overlay_writable_dirs(run_dir: &Path) -> Result<OverlayWritableDirs> {
    // PORT backend/src/sandbox/overlay/writable_dirs.py:48-52 — mkdir upper/work (parents, exist_ok)
    let _ = run_dir;
    todo!()
}
