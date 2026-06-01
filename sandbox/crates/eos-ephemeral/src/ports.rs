//! Inverted port trait shared with deferred plugin dispatch.
//!
//! `eos-plugin` uses this trait for the WRITE_ALLOWED and self-managed PPC paths
//! so both plugin commit routes are forced through the same daemon-owned
//! per-root OCC writer. The runtime implementation is intentionally not in this
//! crate; live shared-workspace execution is owned by `eos-daemon`.

use crate::error::Result;
use eos_protocol::LayerChange;

/// The per-`layer_stack_root` OCC runtime services bundle the daemon injects:
/// the single-writer OCC mutation client + the bound layer-stack snapshot port.
///
/// `eos-daemon` implements this and keys it on `layer_stack_root` so the
/// WRITE_ALLOWED publish path always routes through the ONE `occ-commit-queue`
/// writer per root (MF-1 single-writer).
/// `// PORT backend/src/sandbox/daemon/occ_runtime_services.py:48 — get_occ_runtime_services`
pub trait OccRuntimeServicesPort {
    /// Apply a write/edit changeset through the single OCC writer for this root.
    /// Returns published path results that downstream projection consumes.
    /// `// PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:349 — occ_service.apply_changeset`
    fn apply_changeset(&self, changes: &[LayerChange]) -> Result<Vec<PublishedFile>>;
}

/// Published-file outcome of an OCC changeset apply (path + commit status).
///
/// Compact mirror of eos-occ `changeset.FileResult`. eos-occ owns the concrete
/// publish result and status predicates; this crate keeps only the contract
/// shape needed by deferred plugin PPC dispatch.
/// `// PORT backend/src/sandbox/occ/changeset.py — FileResult`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PublishedFile {
    /// Layer-relative path that was published.
    pub path: String,
    /// Commit status string (e.g. `committed`, `aborted_overlap`).
    pub status: String,
    /// Human-readable detail surfaced as a conflict message on failure.
    pub message: String,
}
