use crate::error::WorkspaceError;
use crate::model::{
    CaptureChangesRequest, CapturedWorkspaceChanges, CreateWorkspaceRequest,
    DestroyWorkspaceRequest, DestroyWorkspaceResult, LatestSnapshotRequest, ReadonlySnapshotHandle,
    RemountWorkspaceRequest, RemountWorkspaceResult, WorkspaceHandle,
};

pub trait WorkspaceService: Send + Sync {
    fn create_workspace(
        &self,
        request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceHandle, WorkspaceError>;

    fn capture_changes(
        &self,
        handle: &WorkspaceHandle,
        request: CaptureChangesRequest,
    ) -> Result<CapturedWorkspaceChanges, WorkspaceError>;

    fn remount_workspace(
        &self,
        handle: &WorkspaceHandle,
        request: RemountWorkspaceRequest,
    ) -> Result<RemountWorkspaceResult, WorkspaceError>;

    fn destroy_workspace(
        &self,
        handle: WorkspaceHandle,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceError>;

    fn latest_snapshot(
        &self,
        request: LatestSnapshotRequest,
    ) -> Result<ReadonlySnapshotHandle, WorkspaceError>;
}
