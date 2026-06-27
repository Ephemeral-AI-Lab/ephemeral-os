use crate::error::WorkspaceError;
use crate::model::{DestroyWorkspaceRequest, DestroyWorkspaceResult, WorkspaceHandle};
use crate::service::support::{active_profile_id, workspace_error_from_profile_error};
use crate::service::WorkspaceRuntimeService;

impl WorkspaceRuntimeService {
    pub fn destroy_workspace(
        &self,
        handle: WorkspaceHandle,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceError> {
        if let Some(hooks) = self.hooks() {
            return (hooks.destroy_workspace)(handle, request);
        }

        let (layer_stack_root, outcome) = {
            let mut state = self.lock_state()?;
            let profile_id = active_profile_id(&state, &handle)?;
            let layer_stack_root = state.layer_stack_root.clone();
            let outcome = match state.manager.exit(&profile_id, request.grace_s) {
                Ok(outcome) => outcome,
                Err(error) => {
                    return Err(workspace_error_from_profile_error(error));
                }
            };
            (layer_stack_root, outcome)
        };

        let release = sandbox_runtime_layerstack::service::release_lease(
            &layer_stack_root,
            &outcome.lease_id,
        );
        let (lease_released, mut lease_release_error) = match release {
            Ok(()) => (Some(true), None),
            Err(error) => (None, Some(error.to_string())),
        };
        let active_leases_after =
            match sandbox_runtime_layerstack::LayerStack::open(layer_stack_root) {
                Ok(stack) => stack.active_lease_count(),
                Err(error) => {
                    let message = format!("count active leases after destroy: {error}");
                    if let Some(existing) = lease_release_error.as_mut() {
                        existing.push_str("; ");
                        existing.push_str(&message);
                    } else {
                        lease_release_error = Some(message);
                    }
                    0
                }
            };

        let result = DestroyWorkspaceResult {
            workspace_session_id: handle.id,
            evicted_upperdir_bytes: outcome.evicted_upperdir_bytes,
            lifetime_s: outcome.lifetime_s,
            lease_released,
            lease_release_error,
            active_leases_after,
        };
        Ok(result)
    }
}
