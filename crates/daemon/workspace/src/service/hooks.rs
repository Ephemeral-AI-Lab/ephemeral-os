use crate::error::WorkspaceError;
use crate::model::{
    CaptureChangesRequest, CapturedWorkspaceChanges, CreateWorkspaceRequest,
    DestroyWorkspaceRequest, DestroyWorkspaceResult, LatestSnapshotRequest, ReadonlySnapshotHandle,
    RemountWorkspaceRequest, RemountWorkspaceResult, WorkspaceHandle,
};

type CreateWorkspaceHook =
    dyn Fn(CreateWorkspaceRequest) -> Result<WorkspaceHandle, WorkspaceError> + Send + Sync;
type CaptureChangesHook = dyn Fn(&WorkspaceHandle, CaptureChangesRequest) -> Result<CapturedWorkspaceChanges, WorkspaceError>
    + Send
    + Sync;
type RemountWorkspaceHook = dyn Fn(&WorkspaceHandle, RemountWorkspaceRequest) -> Result<RemountWorkspaceResult, WorkspaceError>
    + Send
    + Sync;
type DestroyWorkspaceHook = dyn Fn(WorkspaceHandle, DestroyWorkspaceRequest) -> Result<DestroyWorkspaceResult, WorkspaceError>
    + Send
    + Sync;
type LatestSnapshotHook =
    dyn Fn(LatestSnapshotRequest) -> Result<ReadonlySnapshotHandle, WorkspaceError> + Send + Sync;

#[doc(hidden)]
pub struct WorkspaceRuntimeHooks {
    pub create_workspace: Box<CreateWorkspaceHook>,
    pub capture_changes: Box<CaptureChangesHook>,
    pub remount_workspace: Box<RemountWorkspaceHook>,
    pub destroy_workspace: Box<DestroyWorkspaceHook>,
    pub latest_snapshot: Box<LatestSnapshotHook>,
}
