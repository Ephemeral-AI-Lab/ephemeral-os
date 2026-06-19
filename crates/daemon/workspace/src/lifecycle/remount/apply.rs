use crate::lifecycle::leases::monotonic_seconds;
use crate::profile::{IsolatedNetworkError, WorkspaceModeHandle, WorkspaceModeManager};

use super::{RemountPlan, WorkspaceRemountState};

impl WorkspaceModeManager {
    pub fn apply_prepared_remount(
        &mut self,
        plan: RemountPlan,
    ) -> Result<WorkspaceModeHandle, IsolatedNetworkError> {
        let caller_id = plan.caller_id().to_owned();
        let result = self.apply_remount_plan(plan);
        if result.is_err() {
            let _ = self.block_remount(&caller_id);
        }
        result
    }

    fn apply_remount_plan(
        &mut self,
        plan: RemountPlan,
    ) -> Result<WorkspaceModeHandle, IsolatedNetworkError> {
        let (caller_id, layer_paths, probe) = plan.into_parts();
        let workspace_id = self
            .by_caller
            .get(&caller_id)
            .cloned()
            .ok_or(IsolatedNetworkError::NotOpen)?;
        let handle = self
            .handles
            .get(&workspace_id)
            .cloned()
            .ok_or(IsolatedNetworkError::NotOpen)?;
        let remount = self.runtime.remount_overlay(
            &handle,
            &layer_paths,
            &probe,
            self.caps.setup_timeout_s,
        )?;
        if !remount.mount_verified {
            return Err(IsolatedNetworkError::SetupFailed {
                step: format!(
                    "remount overlay verification failed: {}",
                    remount.failure_summary()
                ),
            });
        }
        let updated = self
            .handles
            .get_mut(&workspace_id)
            .ok_or(IsolatedNetworkError::NotOpen)?;
        updated.layer_paths = layer_paths;
        updated.remount_state = WorkspaceRemountState::Active;
        updated.last_activity = monotonic_seconds();
        let updated = updated.clone();
        self.persist_handles()?;
        Ok(updated)
    }
}
