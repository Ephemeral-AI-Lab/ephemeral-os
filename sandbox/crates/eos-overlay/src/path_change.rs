//! Policy-blind path changes captured from a snapshot overlay, plus the
//! ONE-WAY conversion into `eos_protocol::LayerChange`.
//!
//! This conversion lives HERE (occ depends on it one-way; overlay has NO occ
//! dep — the `occ → overlay` edge stays acyclic). The capture half walks ONLY
//! the overlay `upperdir`: capture + publish is one atomic unit per op, so a
//! consumer never observes a partial write set. Other agents never see a
//! half-captured upperdir.

use std::path::Path;

use eos_protocol::{LayerChange, LayerPath};

use crate::error::Result;

/// The kind of a captured overlay path change.
/// `// PORT backend/src/sandbox/overlay/path_change.py:12 — OverlayPathChangeKind`
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OverlayPathChangeKind {
    /// File content write; `content_path` + `final_hash` required.
    Write,
    /// File/dir removal (overlay whiteout).
    Delete,
    /// Symlink; `content_path` (link target capture) + `final_hash` required.
    Symlink,
    /// Opaque-directory marker (root path allowed).
    OpaqueDir,
}

/// A single change captured from the overlay upperdir, before layer-stack
/// policy is applied. `path` is normalized; `write`/`symlink` carry a staged
/// `content_path` + `final_hash`, the others carry neither.
/// `// PORT backend/src/sandbox/overlay/path_change.py:15-35 — OverlayPathChange`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OverlayPathChange {
    /// Normalized relative layer path (root `""` allowed only for `opaque_dir`).
    pub path: LayerPath,
    /// The change kind.
    pub kind: OverlayPathChangeKind,
    /// Staged content path on disk (`write`/`symlink` only).
    pub content_path: Option<String>,
    /// `sha256` hex of the staged content (`write`/`symlink` only).
    pub final_hash: Option<String>,
}

impl OverlayPathChange {
    /// Validate-and-construct exactly as Python `OverlayPathChange.__post_init__`:
    /// normalize the path (root allowed only for `opaque_dir`), require
    /// `content_path`+`final_hash` for `write`/`symlink`, forbid them otherwise.
    /// `// PORT backend/src/sandbox/overlay/path_change.py:22-35 — __post_init__`
    pub fn new(
        path: &str,
        kind: OverlayPathChangeKind,
        content_path: Option<String>,
        final_hash: Option<String>,
    ) -> Result<Self> {
        // PORT backend/src/sandbox/overlay/path_change.py:22-35 — normalize + per-kind field gate
        let _ = (path, kind, content_path, final_hash);
        todo!()
    }

    /// Convert this overlay-side change into the storage-level
    /// `eos_protocol::LayerChange`. ONE-WAY: occ consumes this; overlay never
    /// imports occ. `write` threads the precomputed `content_path`/`final_hash`;
    /// `symlink` reads the link target (`os.readlink`).
    /// `// PORT backend/src/sandbox/occ/overlay_change_conversion.py:19-72 — overlay_path_changes_to_occ_changes`
    pub fn into_layer_change(self) -> Result<LayerChange> {
        // PORT backend/src/sandbox/occ/overlay_change_conversion.py:32-71 — per-kind dispatch
        todo!()
    }
}

/// Walk the overlay `upperdir` and capture the full write set as ordered
/// changes. Walks ONLY the upperdir (never the lower layers): capture + publish
/// is one atomic unit, so the returned set is the complete delta for this op.
/// Overlay whiteouts -> `Delete`, opaque markers -> `OpaqueDir`, symlinks ->
/// `Symlink`, regular files -> `Write`.
/// `// PORT backend/src/sandbox/overlay/capture.py:19-32 — walk_upperdir`
pub fn capture_upperdir(upperdir: &Path) -> Result<Vec<LayerChange>> {
    // PORT backend/src/sandbox/overlay/capture.py:49-89 — _walk_upperdir (os.walk, whiteout/opaque/symlink/write)
    let _ = upperdir;
    todo!()
}
