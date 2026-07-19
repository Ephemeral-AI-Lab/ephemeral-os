use crate::error::WorkspaceError;
use crate::lifecycle::leases::next_handle_id;
use crate::model::{
    CreateWorkspaceRequest, LayerStackSnapshotRef, NetworkProfile, WorkspaceHandle,
    WorkspaceSessionId,
};
use crate::service::support::{ensure_absolute, workspace_error_from_manager_error};
use crate::service::WorkspaceRuntimeService;

impl WorkspaceRuntimeService {
    /// Allocate the identity that the operation layer reserves before any raw
    /// workspace or cgroup resource is created.
    pub fn allocate_workspace_session_id(
        &self,
        network: NetworkProfile,
    ) -> Result<WorkspaceSessionId, WorkspaceError> {
        let _admission = self.admit_work()?;
        if let Some(hooks) = self.hooks() {
            return (hooks.allocate_workspace_session_id)(network);
        }
        Ok(WorkspaceSessionId(next_handle_id()))
    }

    pub fn create_workspace(
        &self,
        request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceHandle, WorkspaceError> {
        let _admission = self.admit_work()?;
        if let Some(hooks) = self.hooks() {
            return (hooks.create_workspace)(request);
        }

        let _ = self.reconcile_pending_teardowns();
        let mut state = self.lock_state()?;
        let layer_stack_root = state.layer_stack_root.clone();
        ensure_absolute(&layer_stack_root, "layer_stack_root")?;
        state
            .manager
            .ensure_workspace_available(&request.workspace_session_id)
            .map_err(workspace_error_from_manager_error)?;

        let snapshot = sandbox_runtime_layerstack::service::acquire_snapshot_with_lease(
            &layer_stack_root,
            "workspace-session",
        )
        .map_err(|error| WorkspaceError::SnapshotAcquire {
            source: error.to_string(),
        })?;
        let snapshot = LayerStackSnapshotRef::from(snapshot);
        let session =
            match state
                .manager
                .open(request.workspace_session_id, snapshot, request.network)
            {
                Ok(handle) => handle,
                Err(error) => return Err(workspace_error_from_manager_error(error)),
            };
        state
            .manager
            .forget_completed_teardowns(&session.workspace_id);
        Ok(WorkspaceHandle::from(&session))
    }
}
