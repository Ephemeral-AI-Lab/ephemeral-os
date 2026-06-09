//! Shared OCC writer facade.
//!
//! The layer-stack-bound `CommitTransactionPort` / `OccRouteProvider` impls plus
//! the gitignore engine and content hashing live in [`eos_occ_layerstack`]; this
//! module keeps the daemon-owned per-root [`service_cache`] single writer and the
//! changeset/base-hash glue that needs the dispatcher's `DaemonError`.

mod service_cache;

use std::path::Path;

use eos_occ::ChangesetResult;
use eos_protocol::{LayerChange, LayerPath};

use crate::config::LayerStackConfig;
use crate::error::DaemonError;

#[cfg(test)]
pub(crate) use eos_occ_layerstack::{
    hash_bytes, LayerStackCommitTransaction, LayerStackRouteProvider,
};
pub(crate) use eos_occ_layerstack::{hash_current, insert_occ_route_timings, occ_route_metrics};
pub(crate) use service_cache::occ_service_cache_snapshot;
#[cfg(test)]
pub(crate) use service_cache::{normalize_root_key, OccServiceCache, OCC_SERVICE_CACHE_MAX};

pub(crate) fn configure_layer_stack(config: &LayerStackConfig) {
    eos_occ_layerstack::configure_auto_squash_max_depth(config.auto_squash_max_depth);
}

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
    Ok(eos_occ_layerstack::base_hashes_for_snapshot(
        root, manifest, changes,
    )?)
}

pub(crate) fn manifest_version_u64(version: i64) -> Result<u64, DaemonError> {
    u64::try_from(version).map_err(|_| {
        DaemonError::LayerStack(eos_layerstack::LayerStackError::Manifest(format!(
            "manifest version must be non-negative: {version}"
        )))
    })
}
