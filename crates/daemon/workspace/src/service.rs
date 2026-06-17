use crate::error::WorkspaceError;
use crate::model::{
    CaptureChangesRequest, CaptureChangesResult, CreateWorkspaceRequest, DestroyWorkspaceRequest,
    DestroyWorkspaceResult, RunCommandRequest, RunCommandResult, WorkspaceHandle,
};

pub trait WorkspaceService {
    fn create(&self, request: CreateWorkspaceRequest) -> Result<WorkspaceHandle, WorkspaceError>;

    fn run_command(
        &self,
        handle: &WorkspaceHandle,
        request: RunCommandRequest,
    ) -> Result<RunCommandResult, WorkspaceError>;

    fn capture_changes(
        &self,
        handle: &WorkspaceHandle,
        request: CaptureChangesRequest,
    ) -> Result<CaptureChangesResult, WorkspaceError>;

    fn destroy(
        &self,
        handle: WorkspaceHandle,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceError>;
}
