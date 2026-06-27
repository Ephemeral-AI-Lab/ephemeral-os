use crate::error::WorkspaceError;
use crate::model::{CreateWorkspaceRequest, LayerStackSnapshotRef, WorkspaceHandle};
use crate::service::support::{ensure_absolute, workspace_error_from_profile_error};
use crate::service::WorkspaceRuntimeService;

impl WorkspaceRuntimeService {
    pub fn create_workspace(
        &self,
        request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceHandle, WorkspaceError> {
        if let Some(hooks) = self.hooks() {
            return (hooks.create_workspace)(request);
        }

        let mut state = self.lock_state()?;
        let layer_stack_root = state.layer_stack_root.clone();
        ensure_absolute(&layer_stack_root, "layer_stack_root")?;

        let snapshot = sandbox_runtime_layerstack::service::acquire_snapshot_with_lease(
            &layer_stack_root,
            "workspace-session",
        )
        .map_err(|error| WorkspaceError::SnapshotAcquire {
            source: error.to_string(),
        })?;
        let lease_id = snapshot.lease_id.clone();
        let profile_snapshot = LayerStackSnapshotRef::from(snapshot);
        let profile_handle = match state
            .manager
            .enter_with_profile(profile_snapshot, request.profile)
        {
            Ok(handle) => handle,
            Err(error) => {
                let _ = sandbox_runtime_layerstack::service::release_lease(
                    &layer_stack_root,
                    &lease_id,
                );
                return Err(workspace_error_from_profile_error(error));
            }
        };
        Ok(WorkspaceHandle::from(&profile_handle))
    }
}
