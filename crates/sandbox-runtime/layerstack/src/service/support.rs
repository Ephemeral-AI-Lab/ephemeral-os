use std::path::Path;

use crate::model::{manifest_root_hash, Manifest};

use super::model::Snapshot;

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
