use eos_protocol::LayerChange;

use crate::error::EphemeralWorkspaceError;
use crate::types::{EphemeralSnapshot, PathChange, PublishOutcome, WorkspaceRoot};

/// Snapshot/lease port supplied by the daemon's LayerStack adapter.
pub trait EphemeralSnapshotPort {
    fn acquire_snapshot(
        &self,
        root: &WorkspaceRoot,
        request_id: &str,
    ) -> Result<EphemeralSnapshot, EphemeralWorkspaceError>;

    fn release_lease(
        &self,
        root: &WorkspaceRoot,
        lease_id: &str,
    ) -> Result<bool, EphemeralWorkspaceError>;
}

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

/// Fresh namespace runner port supplied by daemon process supervision.
pub trait FreshNamespaceRunnerPort {
    fn run(
        &self,
        request: &eos_runner::RunRequest,
    ) -> Result<eos_runner::RunResult, EphemeralWorkspaceError>;
}
