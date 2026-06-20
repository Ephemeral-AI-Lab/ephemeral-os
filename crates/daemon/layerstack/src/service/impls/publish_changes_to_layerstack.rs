use crate::commit::route::publish_decisions_for_manifest;
use crate::commit::{ChangesetResult, CommitError};

use super::super::cache::service_for_root;
use super::super::model::PublishChangesRequest;
use super::super::support::{manifest_version_u64, snapshot_manifest};

pub fn publish_changes_to_layerstack(
    request: PublishChangesRequest<'_>,
) -> Result<ChangesetResult, CommitError> {
    let manifest = snapshot_manifest(
        request.root,
        request.snapshot_manifest_version,
        request.snapshot_layer_paths,
    )?;
    let decisions = publish_decisions_for_manifest(request.root, &manifest, request.changes)?;
    service_for_root(request.root)?.apply_layerstack_changeset(
        request.changes,
        Some(manifest_version_u64(request.snapshot_manifest_version)?),
        decisions,
    )
}
