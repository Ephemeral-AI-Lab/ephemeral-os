use std::sync::PoisonError;

use crate::workspace_crate::{DestroyWorkspaceRequest, DestroyWorkspaceResult, WorkspaceSessionId};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

impl WorkspaceSessionService {
    /// Guarded explicit destroy: hold the session admission gate, refuse while
    /// the command ledger is non-empty, otherwise snapshot-and-destroy without
    /// publishing regardless of policy. Sessions in `finalize_failed` or stuck
    /// `finalizing` state pass the ledger check and are destroyed — this is
    /// the recovery path for a failed finalize (§2.5).
    ///
    /// # Errors
    /// Returns [`WorkspaceSessionError::ActiveCommands`] while any command
    /// runs in the session, [`WorkspaceSessionError::NotFound`] for an unknown
    /// session, or the teardown failure.
    pub fn guarded_destroy(
        &self,
        workspace_session_id: WorkspaceSessionId,
        grace_s: Option<f64>,
    ) -> Result<DestroyWorkspaceResult, WorkspaceSessionError> {
        let gate = self.session_gate(&workspace_session_id);
        let _admission = gate.lock().unwrap_or_else(PoisonError::into_inner);
        let handler = {
            let sessions = self.lock_sessions()?;
            let Some(session) = sessions.get(&workspace_session_id) else {
                drop(sessions);
                self.discard_resurrected_gate(&workspace_session_id, &gate);
                return Err(WorkspaceSessionError::not_found(&workspace_session_id));
            };
            if !session.active_commands.is_empty() {
                return Err(WorkspaceSessionError::ActiveCommands {
                    workspace_session_id,
                    active_command_session_ids: session.active_commands.iter().cloned().collect(),
                });
            }
            session.handler()
        };
        self.destroy_session(handler, DestroyWorkspaceRequest { grace_s })
    }
}
