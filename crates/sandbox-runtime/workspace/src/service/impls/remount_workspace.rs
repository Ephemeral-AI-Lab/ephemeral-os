use std::path::PathBuf;

use crate::error::WorkspaceError;
use crate::lifecycle::{ReapedSession, RemountOutcome};
use crate::model::WorkspaceSessionId;
use crate::service::support::workspace_error_from_manager_error;
use crate::service::WorkspaceRuntimeService;
use crate::session::WorkspaceManagerError;

impl WorkspaceRuntimeService {
    /// Run the live remount transaction for one session. `Ok(None)` means
    /// the session is gone (the caller's silent skip); every other outcome
    /// follows the C1/C5 rules inside [`RemountOutcome`].
    ///
    /// # Errors
    ///
    /// Returns [`WorkspaceError`] when the runtime state lock is
    /// unavailable or the transaction hits a setup failure before it can
    /// classify.
    pub fn remount_workspace(
        &self,
        workspace_session_id: &WorkspaceSessionId,
        cgroup_procs_path: Option<PathBuf>,
    ) -> Result<Option<RemountOutcome>, WorkspaceError> {
        if self.hooks().is_some() {
            return Err(WorkspaceError::Setup {
                step: "workspace runtime hooks do not implement remount".to_owned(),
            });
        }
        let mut state = self.lock_state()?;
        let layer_stack_root = state.layer_stack_root.clone();
        match state.manager.remount_session(
            &layer_stack_root,
            workspace_session_id,
            cgroup_procs_path,
        ) {
            Ok(outcome) => Ok(Some(outcome)),
            Err(WorkspaceManagerError::NotOpen) => Ok(None),
            Err(error) => Err(workspace_error_from_manager_error(error)),
        }
    }

    /// The authoritative post-remount handle for a live session, or `None`
    /// when the session is gone — the operation layer refreshes its
    /// registry copy from this after a switch.
    ///
    /// # Errors
    ///
    /// Returns [`WorkspaceError`] when the runtime state lock is
    /// unavailable.
    pub fn current_handle(
        &self,
        workspace_session_id: &WorkspaceSessionId,
    ) -> Result<Option<crate::model::WorkspaceHandle>, WorkspaceError> {
        if self.hooks().is_some() {
            return Ok(None);
        }
        let state = self.lock_state()?;
        Ok(state
            .manager
            .handle(workspace_session_id)
            .map(crate::model::WorkspaceHandle::from))
    }

    /// Boot reap: destroy every persisted handle's run dir and reset the
    /// handle file — every persisted session is provably dead (PDEATHSIG).
    ///
    /// # Errors
    ///
    /// Returns [`WorkspaceError`] when the runtime state lock is
    /// unavailable; hook-backed services reap nothing.
    pub fn reap_persisted_sessions(&self) -> Result<Vec<ReapedSession>, WorkspaceError> {
        if self.hooks().is_some() {
            return Ok(Vec::new());
        }
        Ok(self.lock_state()?.manager.reap_persisted_handles())
    }
}
