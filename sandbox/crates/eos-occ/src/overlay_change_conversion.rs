//! The ONE-WAY occ -> overlay edge: convert overlay captures into OCC changes.
//!
//! This is the single reason `eos-occ` links `eos-overlay`. The edge is
//! strictly one-way (overlay never links occ), which keeps the occ/overlay axis
//! acyclic. Nothing else in this crate touches overlay.

use eos_protocol::LayerChange;

use crate::error::OccError;

/// Policy-blind overlay capture for one path.
///
/// Local placeholder for `eos_overlay::OverlayPathChange` (the sibling crate is
/// being written concurrently and exports nothing yet). When `eos-overlay`
/// publishes its `path_change` type, this struct is replaced by an import of
/// that type — this is the only overlay item `eos-occ` consumes.
// PORT backend/src/sandbox/overlay/path_change.py — OverlayPathChange (the one-way import target)
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OverlayPathChange {
    /// Capture kind: `write` / `delete` / `symlink` / `opaque_dir`.
    pub kind: String,
    /// Workspace-relative path the capture touched.
    pub path: String,
    /// Staged content file (write/symlink kinds), if any.
    pub content_path: Option<String>,
    /// Precomputed content hash (write kind), if any.
    pub final_hash: Option<String>,
}

/// Convert policy-blind overlay captures into typed OCC mutations.
///
/// `write` kinds thread the already-staged `content_path` + precomputed
/// `final_hash` into the change (the OCC stager copies in-kernel and reuses the
/// hash rather than re-reading bytes here). A `write` missing either field is an
/// [`OccError::InvalidOverlayChange`].
// PORT backend/src/sandbox/occ/overlay_change_conversion.py:16 — overlay.path_change -> OCC changes
pub fn overlay_path_changes_to_occ_changes(
    path_changes: &[OverlayPathChange],
) -> Result<Vec<LayerChange>, OccError> {
    let _ = path_changes;
    todo!("PORT occ/overlay_change_conversion.py:16 — map OverlayPathChange kinds to LayerChange")
}
