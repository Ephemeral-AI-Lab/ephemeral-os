use crate::error::WorkspaceError;
use crate::model::{DestroyWorkspaceRequest, DestroyWorkspaceResult, WorkspaceHandle};
use crate::service::support::workspace_error_from_manager_error;
use crate::service::WorkspaceRuntimeService;
use crate::session::ExitOutcome;

impl WorkspaceRuntimeService {
    pub fn destroy_workspace(
        &self,
        handle: WorkspaceHandle,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceError> {
        if let Some(hooks) = self.hooks() {
            return (hooks.destroy_workspace)(handle, request);
        }

        let outcome = {
            let mut state = self.lock_state()?;
            match state.manager.close(&handle.id, request.grace_s) {
                Ok(outcome) => outcome,
                Err(error) => {
                    return Err(workspace_error_from_manager_error(error));
                }
            }
        };

        Ok(destroy_result_from_outcome(outcome))
    }

    /// Retry every retained raw-workspace teardown transaction once. Each
    /// result is independent so one failed cleanup cannot prevent peer
    /// transactions from making progress; callers may invoke this after a
    /// repair or from an event-driven holder reconciliation pass.
    #[doc(hidden)]
    pub fn reconcile_pending_teardowns(
        &self,
    ) -> Result<Vec<Result<DestroyWorkspaceResult, WorkspaceError>>, WorkspaceError> {
        if self.hooks().is_some() {
            return Ok(Vec::new());
        }

        let mut state = self.lock_state()?;
        let ids = state.manager.pending_teardown_ids();
        Ok(ids
            .into_iter()
            .map(|workspace_id| {
                state
                    .manager
                    .close(&workspace_id, None)
                    .map(destroy_result_from_outcome)
                    .map_err(workspace_error_from_manager_error)
            })
            .collect())
    }
}

fn destroy_result_from_outcome(outcome: ExitOutcome) -> DestroyWorkspaceResult {
    DestroyWorkspaceResult {
        workspace_session_id: outcome.workspace_id,
        evicted_upperdir_bytes: outcome.evicted_upperdir_bytes,
        lifetime_s: outcome.lifetime_s,
        lease_released: Some(true),
        lease_release_error: None,
        active_leases_after: outcome.active_leases_after,
    }
}
