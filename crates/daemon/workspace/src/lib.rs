//! Shared workspace runtime primitives plus concrete workspace modes.
//!
//! `network_mode::host` owns one-operation Host overlay transactions.
//! `network_mode::isolated_network` owns caller-keyed private namespaces whose
//! upperdir is discarded on exit. `overlay` holds the filesystem and telemetry
//! contracts both modes share so they expose the same core operation vocabulary
//! without hiding their different lifecycle rules.
#![forbid(unsafe_code)]

pub mod error;
mod isolated_network_setup;
mod lifecycle;
pub mod model;
mod namespace;
pub mod network_mode;
pub mod overlay;
pub mod service;

pub use error::WorkspaceError;
pub use model::{
    BaseRevision, CallerId, CaptureChangesRequest, CaptureChangesResult, CapturedWorkspaceChanges,
    ChangedPathKind, CommandStatus, CreateWorkspaceRequest, DestroyWorkspaceRequest,
    DestroyWorkspaceResult, LatestSnapshotRequest, LayerStackSnapshotRef, LeaseId, NetworkMode,
    ProtectedPathDrop, ProtectedPathDropReason, ReadonlySnapshotHandle, RemountWorkspaceRequest,
    RemountWorkspaceResult, RunCommandRequest, RunCommandResult, WorkspaceHandle,
    WorkspaceHandle as UnifiedWorkspaceHandle, WorkspaceId,
};
pub use service::WorkspaceService;

#[cfg(test)]
mod tests {
    #[test]
    fn root_handle_export_is_unified_public_handle() {
        fn unified_alias(handle: crate::UnifiedWorkspaceHandle) -> crate::model::WorkspaceHandle {
            handle
        }

        let _: fn(crate::WorkspaceHandle) -> crate::model::WorkspaceHandle = unified_alias;
        let _: fn(crate::UnifiedWorkspaceHandle) -> crate::model::WorkspaceHandle = unified_alias;
    }
}
