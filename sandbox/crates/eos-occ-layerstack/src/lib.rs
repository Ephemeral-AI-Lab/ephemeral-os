//! Layer-stack-bound OCC adapters.
//!
//! This is the intermediate host crate that binds the route-agnostic OCC engine
//! (`eos-occ`) to concrete `eos-layerstack` storage: `eos-daemon` → this crate →
//! {`eos-occ`, `eos-layerstack`}. It owns the two pieces of OCC machinery that
//! `eos-occ` deliberately cannot link (it depends on `eos-protocol` only):
//!
//! * [`LayerStackCommitTransaction`] — the [`eos_occ::CommitTransactionPort`]
//!   impl that revalidates a prepared changeset against the active manifest and
//!   publishes a new layer (with auto-squash) via `LayerStack`.
//! * [`LayerStackRouteProvider`] — the [`eos_occ::OccRouteProvider`] impl plus
//!   the gitignore engine and [`occ_route_metrics`] telemetry.
//!
//! The OCC single-writer cache (`OccService` per root) stays daemon-owned; this
//! crate is *reuse only* and gains no dependency toward the daemon. Errors are
//! `eos-layerstack`/`eos-occ` native — there is no `DaemonError` edge here.
#![forbid(unsafe_code)]

mod publish;
mod route;

use std::path::Path;

use eos_layerstack::{LayerStackError, Manifest, MergedView};
use eos_protocol::{LayerChange, LayerPath};
use sha2::{Digest, Sha256};

pub use publish::{configure_auto_squash_max_depth, LayerStackCommitTransaction};
pub use route::{
    insert_occ_route_timings, occ_route_metrics, LayerStackRouteProvider, OccRouteMetrics,
};

/// Per-path base-hash overrides for a snapshot's changeset.
///
/// Builds a [`MergedView`] over `root` and, for each non-`OpaqueDir` change,
/// hashes the bytes visible at `manifest` via [`hash_current`] so OCC publish
/// can gate on the base the writer observed. `OpaqueDir` (and absent) paths map
/// to `None`. Errors are native [`LayerStackError`] — no daemon edge.
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
            Ok((change.path().clone(), hash_current(bytes.as_deref(), exists)))
        })
        .collect()
}

/// SHA-256 of `content`, lowercase hex, when `exists` is true.
///
/// Returns `None` for an absent path so an absent base and an empty-but-present
/// file stay distinguishable in OCC base-hash gating.
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

fn usize_to_f64_saturating(value: usize) -> f64 {
    u32::try_from(value).map_or_else(|_| f64::from(u32::MAX), f64::from)
}

fn i64_to_f64_saturating(value: i64) -> f64 {
    u64::try_from(value).map_or(0.0, |value| {
        u32::try_from(value).map_or_else(|_| f64::from(u32::MAX), f64::from)
    })
}
