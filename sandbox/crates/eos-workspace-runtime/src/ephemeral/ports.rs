use eos_protocol::LayerChange;

use crate::contract::SnapshotLease;
use crate::ephemeral::error::EphemeralWorkspaceError;
use crate::ephemeral::types::{LayerStackRoot, PathChange, PublishOutcome};

/// Publisher port supplied by the daemon's neutral OCC publisher adapter.
pub trait WorkspacePublisherPort {
    fn publish_upperdir_changes(
        &self,
        root: &LayerStackRoot,
        snapshot: &SnapshotLease,
        changes: &[LayerChange],
        path_kinds: &[PathChange],
    ) -> Result<PublishOutcome, EphemeralWorkspaceError>;
}
