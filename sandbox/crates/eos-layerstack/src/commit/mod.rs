//! Optimistic-concurrency commit: the single-writer publish gate per root.
//!
//! Invariant (MF-1): the commit path owns the publish DECISION gate — N
//! disjoint file-API writes batch into ONE manifest CAS attempt; each
//! normalized path routes to exactly one of [`Route::Drop`] (`.git`),
//! [`Route::Direct`] (gitignored), [`Route::Gated`] (tracked, base-hash
//! checked), or [`Route::Reject`] (disallowed). A stale base surfaces
//! [`CommitStatus::AbortedVersion`] after the bounded CAS retry. EXACTLY ONE
//! `occ-commit-queue` writer per `layer_stack_root` serializes all publishes:
//! any second commit entry point (e.g. the PPC self-managed plugin callback)
//! MUST route through this same single writer, never a second
//! [`CommitQueue`] instance — [`crate::service`] owns that per-root registry.
//!
//! Everything here except the outcome vocabulary and the hash helpers is
//! crate-internal machinery behind the [`crate::service`] facade.

pub mod error;
pub mod outcome;
pub mod prepare;
pub mod queue;
pub mod transaction;

use std::path::Path;

use crate::model::{LayerChange, LayerPath};
use sha2::{Digest, Sha256};

use crate::{LayerStackError, Manifest, MergedView};

pub use error::CommitError;
pub use outcome::{ChangesetResult, CommitStatus, FileResult, Route};
pub use prepare::CommitService;
pub use queue::CommitQueue;
pub use transaction::{configure_auto_squash_max_depth, CommitTransaction};

/// Per-path base-hash overrides for a snapshot's changeset.
///
/// Builds a [`MergedView`] over `root` and, for each non-`OpaqueDir` change,
/// hashes the bytes visible at `manifest` via [`hash_current`] so the commit
/// gate validates against the base the writer observed. `OpaqueDir` (and
/// absent) paths map to `None`.
pub fn base_hashes_for_snapshot(
    root: &Path,
    manifest: &Manifest,
    changes: &[LayerChange],
) -> Result<Vec<(LayerPath, Option<String>)>, LayerStackError> {
    let view = MergedView::new(root.to_path_buf());
    changes
        .iter()
        .map(|change| {
            if matches!(change, LayerChange::OpaqueDir { .. }) {
                return Ok((change.path().clone(), None));
            }
            let (bytes, exists) = view.read_bytes(change.path().as_str(), manifest)?;
            Ok((
                change.path().clone(),
                hash_current(bytes.as_deref(), exists),
            ))
        })
        .collect()
}

/// SHA-256 of `content`, lowercase hex, when `exists` is true.
///
/// Returns `None` for an absent path so an absent base and an empty-but-present
/// file stay distinguishable in base-hash gating.
#[must_use]
pub fn hash_current(content: Option<&[u8]>, exists: bool) -> Option<String> {
    if !exists {
        return None;
    }
    content.map(hash_bytes)
}

/// Lowercase-hex SHA-256 of `content`.
#[must_use]
pub fn hash_bytes(content: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(content);
    hex_lower(&hasher.finalize())
}

fn hex_lower(bytes: &[u8]) -> String {
    const LOWER_HEX: &[u8; 16] = b"0123456789abcdef";

    let mut out = String::with_capacity(bytes.len() * 2);
    for &byte in bytes {
        out.push(char::from(LOWER_HEX[usize::from(byte >> 4)]));
        out.push(char::from(LOWER_HEX[usize::from(byte & 0x0f)]));
    }
    out
}

pub(crate) fn usize_to_f64_saturating(value: usize) -> f64 {
    u32::try_from(value).map_or_else(|_| f64::from(u32::MAX), f64::from)
}

pub(crate) fn i64_to_f64_saturating(value: i64) -> f64 {
    u64::try_from(value).map_or(0.0, |value| {
        u32::try_from(value).map_or_else(|_| f64::from(u32::MAX), f64::from)
    })
}
