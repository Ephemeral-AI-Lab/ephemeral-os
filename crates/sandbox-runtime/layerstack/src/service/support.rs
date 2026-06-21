use std::path::{Path, PathBuf};

use crate::model::{manifest_root_hash, Manifest};
use crate::Lease;

use super::model::{LeasedSnapshot, Snapshot};

pub(super) fn snapshot_from_manifest(root: &Path, manifest: Manifest) -> Snapshot {
    Snapshot {
        manifest_version: manifest.version,
        root_hash: manifest_root_hash(&manifest),
        layer_paths: manifest
            .layers
            .iter()
            .map(|layer| crate::fs::resolve_layer_path(root, &layer.path))
            .collect(),
    }
}

pub(super) fn snapshot_from_lease(lease: Lease) -> LeasedSnapshot {
    LeasedSnapshot {
        lease_id: lease.lease_id,
        manifest_version: lease.manifest_version,
        root_hash: lease.root_hash,
        manifest: lease.manifest,
        layer_paths: lease.layer_paths.into_iter().map(PathBuf::from).collect(),
    }
}
