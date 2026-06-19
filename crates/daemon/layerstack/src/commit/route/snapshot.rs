use sha2::{Digest, Sha256};

use crate::model::{hex_lower, LayerChange, LayerPath};
use crate::{Manifest, MergedView};

use super::super::error::CommitError;

pub(super) fn snapshot_base_hash(
    view: &MergedView,
    manifest: &Manifest,
    change: &LayerChange,
) -> Result<Option<String>, CommitError> {
    if matches!(change, LayerChange::OpaqueDir { .. }) {
        return Ok(None);
    }
    snapshot_base_hash_for_path(view, manifest, change.path())
}

pub(super) fn snapshot_base_hash_for_path(
    view: &MergedView,
    manifest: &Manifest,
    path: &LayerPath,
) -> Result<Option<String>, CommitError> {
    let (bytes, exists) = view
        .read_bytes(path.as_str(), manifest)
        .map_err(|err| CommitError::RoutePreparation(err.to_string()))?;
    Ok(hash_current(bytes.as_deref(), exists))
}

pub(crate) fn hash_current(content: Option<&[u8]>, exists: bool) -> Option<String> {
    if !exists {
        return None;
    }
    content.map(|content| {
        let mut hasher = Sha256::new();
        hasher.update(content);
        hex_lower(hasher.finalize())
    })
}
