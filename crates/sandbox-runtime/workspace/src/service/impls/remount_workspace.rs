use crate::error::WorkspaceError;
use crate::model::{RemountWorkspaceRequest, RemountWorkspaceResult, WorkspaceHandle};
use crate::profile::RemountProbe;
use crate::service::support::{active_mode_id, workspace_error_from_mode_error};
use crate::service::WorkspaceRuntimeService;

impl WorkspaceRuntimeService {
    pub fn remount_workspace(
        &self,
        handle: &WorkspaceHandle,
        request: RemountWorkspaceRequest,
    ) -> Result<RemountWorkspaceResult, WorkspaceError> {
        if let Some(hooks) = self.hooks() {
            return (hooks.remount_workspace)(handle, request);
        }

        if request.layer_paths.is_empty() {
            return Err(WorkspaceError::InvalidRequest {
                field: "layer_paths",
                message: "must not be empty".to_owned(),
            });
        }

        let mut state = self.lock_state()?;
        let mode_id = active_mode_id(&state, handle)?;
        let remounted = state
            .manager
            .remount_with_layers(&mode_id, request.layer_paths, &RemountProbe::default())
            .map_err(workspace_error_from_mode_error)?;
        Ok(RemountWorkspaceResult {
            handle: WorkspaceHandle::from(&remounted),
        })
    }
}
