use sandbox_runtime_namespace_process::runner::file_op::FileRunnerOp;
use sandbox_runtime_namespace_process::runner::protocol::RunResult;

use crate::error::WorkspaceError;
use crate::model::{
    CaptureChangesRequest, CapturedWorkspaceChanges, CreateWorkspaceRequest,
    DestroyWorkspaceRequest, DestroyWorkspaceResult, NetworkProfile, ReadonlySnapshotHandle,
    WorkspaceHandle, WorkspaceSessionId,
};
use crate::namespace::holder::{HolderFinalization, HolderFinalizationProof, HolderProbe};
use crate::service::HolderExitSubscription;

#[doc(hidden)]
pub struct WorkspaceRuntimeHooks {
    pub take_holder_exit_subscription:
        Box<dyn Fn() -> Result<Option<HolderExitSubscription>, WorkspaceError> + Send + Sync>,
    #[expect(
        clippy::type_complexity,
        reason = "hook signatures stay explicit by policy"
    )]
    pub isolated_ip: Box<
        dyn Fn(&WorkspaceSessionId) -> Result<Option<std::net::Ipv4Addr>, WorkspaceError>
            + Send
            + Sync,
    >,
    pub holder_is_live: Box<dyn Fn(&WorkspaceHandle) -> bool + Send + Sync>,
    pub holder_probe: Box<dyn Fn(&WorkspaceHandle) -> HolderProbe + Send + Sync>,
    pub holder_finalization: Box<dyn Fn(&WorkspaceHandle) -> HolderFinalization + Send + Sync>,
    #[expect(
        clippy::type_complexity,
        reason = "hook signatures stay explicit by policy"
    )]
    pub holder_exit_reason: Box<dyn Fn(&WorkspaceHandle) -> Option<String> + Send + Sync>,
    #[expect(
        clippy::type_complexity,
        reason = "hook signatures stay explicit by policy"
    )]
    pub run_file_op: Box<
        dyn Fn(&WorkspaceHandle, FileRunnerOp) -> Result<RunResult, WorkspaceError> + Send + Sync,
    >,
    pub allocate_workspace_session_id:
        Box<dyn Fn(NetworkProfile) -> Result<WorkspaceSessionId, WorkspaceError> + Send + Sync>,
    pub create_workspace: Box<
        dyn Fn(CreateWorkspaceRequest) -> Result<WorkspaceHandle, WorkspaceError> + Send + Sync,
    >,
    #[expect(
        clippy::type_complexity,
        reason = "hook signatures stay explicit by policy"
    )]
    pub capture_changes: Box<
        dyn Fn(
                &WorkspaceHandle,
                CaptureChangesRequest,
            ) -> Result<CapturedWorkspaceChanges, WorkspaceError>
            + Send
            + Sync,
    >,
    #[expect(
        clippy::type_complexity,
        reason = "hook signatures stay explicit by policy"
    )]
    pub capture_changes_after_holder_quiesced: Box<
        dyn Fn(
                &WorkspaceHandle,
                &HolderFinalizationProof,
                CaptureChangesRequest,
            ) -> Result<CapturedWorkspaceChanges, WorkspaceError>
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
    pub commit_workspace_destroy: Box<dyn Fn(&WorkspaceHandle) + Send + Sync>,
    pub latest_snapshot:
        Box<dyn Fn() -> Result<ReadonlySnapshotHandle, WorkspaceError> + Send + Sync>,
}
