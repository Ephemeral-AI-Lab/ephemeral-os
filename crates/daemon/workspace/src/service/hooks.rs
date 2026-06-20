use crate::error::WorkspaceError;
use crate::model::{
    CaptureChangesRequest, CapturedWorkspaceChanges, CreateWorkspaceRequest,
    DestroyWorkspaceRequest, DestroyWorkspaceResult, LatestSnapshotRequest, ReadonlySnapshotHandle,
    RemountWorkspaceRequest, RemountWorkspaceResult, WorkspaceHandle,
};

#[doc(hidden)]
pub struct WorkspaceRuntimeHooks {
    pub create_workspace: Box<
        dyn Fn(CreateWorkspaceRequest) -> Result<WorkspaceHandle, WorkspaceError> + Send + Sync,
    >,
    pub capture_changes: Box<
        dyn Fn(
                &WorkspaceHandle,
                CaptureChangesRequest,
            ) -> Result<CapturedWorkspaceChanges, WorkspaceError>
            + Send
            + Sync,
    >,
    pub remount_workspace: Box<
        dyn Fn(
                &WorkspaceHandle,
                RemountWorkspaceRequest,
            ) -> Result<RemountWorkspaceResult, WorkspaceError>
            + Send
            + Sync,
    >,
    pub destroy_workspace: Box<
        dyn Fn(
                WorkspaceHandle,
                DestroyWorkspaceRequest,
            ) -> Result<DestroyWorkspaceResult, WorkspaceError>
            + Send
            + Sync,
    >,
    pub latest_snapshot: Box<
        dyn Fn(LatestSnapshotRequest) -> Result<ReadonlySnapshotHandle, WorkspaceError>
            + Send
            + Sync,
    >,
}
