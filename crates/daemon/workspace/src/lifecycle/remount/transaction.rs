use crate::lifecycle::leases::monotonic_seconds;
use crate::profile::{IsolatedNetworkError, WorkspaceModeManager};

use super::{RemountPlan, RemountProbe, WorkspaceRemountState};

impl WorkspaceModeManager {
    fn prepare_remount(
        &mut self,
        caller_id: &str,
        layer_paths: Vec<std::path::PathBuf>,
        probe: RemountProbe,
    ) -> Result<RemountPlan, IsolatedNetworkError> {
        let plan = RemountPlan::new(caller_id.to_owned(), layer_paths, probe)?;
        let workspace_id = self
            .by_caller
            .get(plan.caller_id())
            .cloned()
            .ok_or(IsolatedNetworkError::NotOpen)?;
        if !self.handles.contains_key(&workspace_id) {
            return Err(IsolatedNetworkError::NotOpen);
        }
        self.set_remount_state(plan.caller_id(), WorkspaceRemountState::Pending)?;
        Ok(plan)
    }

    pub(crate) fn block_remount(&mut self, caller_id: &str) -> Result<(), IsolatedNetworkError> {
        self.set_remount_state(caller_id, WorkspaceRemountState::Active)
    }

    pub(crate) fn remount_with_layers(
        &mut self,
        caller_id: &str,
        layer_paths: Vec<std::path::PathBuf>,
        probe: &RemountProbe,
    ) -> Result<crate::profile::WorkspaceModeHandle, IsolatedNetworkError> {
        let plan = self.prepare_remount(caller_id, layer_paths, probe.clone())?;
        self.apply_prepared_remount(plan)
    }

    pub(super) fn set_remount_state(
        &mut self,
        caller_id: &str,
        remount_state: WorkspaceRemountState,
    ) -> Result<(), IsolatedNetworkError> {
        if caller_id.trim().is_empty() {
            return Err(IsolatedNetworkError::InvalidArgument(
                "caller_id is required".to_owned(),
            ));
        }
        let workspace_id = self
            .by_caller
            .get(caller_id)
            .cloned()
            .ok_or(IsolatedNetworkError::NotOpen)?;
        let handle = self
            .handles
            .get_mut(&workspace_id)
            .ok_or(IsolatedNetworkError::NotOpen)?;
        if handle.remount_state == remount_state {
            return Ok(());
        }
        handle.remount_state = remount_state;
        handle.last_activity = monotonic_seconds();
        self.persist_handles()
    }
}
