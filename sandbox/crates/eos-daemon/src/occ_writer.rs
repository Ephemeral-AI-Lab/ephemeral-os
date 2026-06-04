//! Shared OCC writer facade.

mod publish;
mod route;
mod service_cache;

use std::path::Path;

use eos_layerstack::MergedView;
use eos_occ::ChangesetResult;
use eos_protocol::{LayerChange, LayerPath};
use sha2::{Digest, Sha256};

use crate::error::DaemonError;

#[cfg(test)]
pub(crate) use publish::LayerStackCommitTransaction;
#[cfg(test)]
pub(crate) use route::LayerStackRouteProvider;
pub(crate) use route::{insert_occ_route_timings, occ_route_metrics, OccRouteMetrics};
pub(crate) use service_cache::occ_service_cache_snapshot;
#[cfg(test)]
pub(crate) use service_cache::{normalize_root_key, OccServiceCache, OCC_SERVICE_CACHE_MAX};

pub(crate) fn apply_occ_changeset(
    root: &Path,
    snapshot_version: Option<u64>,
    changes: &[LayerChange],
    base_hashes: &[(LayerPath, Option<String>)],
) -> Result<ChangesetResult, DaemonError> {
    let lookup = service_cache::occ_service_for_root(root)?;
    let mut result = lookup.service.apply_changeset_with_base_hashes(
        changes,
        snapshot_version,
        true,
        base_hashes,
    )?;
    lookup.insert_timings(&mut result.timings);
    Ok(result)
}

pub(crate) fn base_hashes_for_snapshot(
    root: &Path,
    manifest: &eos_layerstack::Manifest,
    changes: &[LayerChange],
) -> Result<Vec<(LayerPath, Option<String>)>, DaemonError> {
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

pub(crate) fn hash_current(content: Option<&[u8]>, exists: bool) -> Option<String> {
    if !exists {
        return None;
    }
    content.map(hash_bytes)
}

pub(crate) fn hash_bytes(content: &[u8]) -> String {
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

pub(crate) fn manifest_version_u64(version: i64) -> Result<u64, DaemonError> {
    u64::try_from(version).map_err(|_| {
        DaemonError::LayerStack(eos_layerstack::LayerStackError::Manifest(format!(
            "manifest version must be non-negative: {version}"
        )))
    })
}
