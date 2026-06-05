use eos_protocol::LayerChange;

use crate::error::EphemeralWorkspaceError;
use crate::types::{EphemeralSnapshot, PathChange, PublishOutcome, WorkspaceRoot};

/// Publisher port supplied by the daemon's neutral OCC publisher adapter.
pub trait WorkspacePublisherPort {
    fn publish_upperdir_changes(
        &self,
        root: &WorkspaceRoot,
        snapshot: &EphemeralSnapshot,
        changes: &[LayerChange],
        path_kinds: &[PathChange],
    ) -> Result<PublishOutcome, EphemeralWorkspaceError>;
}
