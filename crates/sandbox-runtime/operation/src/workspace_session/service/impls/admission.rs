use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock, PoisonError};

use sandbox_observability_telemetry::record::names;
use sandbox_runtime_namespace_execution::NamespaceExecutionId;
use serde_json::json;

use crate::workspace_crate::WorkspaceSessionId;
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

use super::super::model::{
    FinalizationState, FinalizeOutcome, FinalizePolicy, WorkspaceSessionHandler,
};

/// Take-once slot shared between `exec_command`'s failure path and the engine
/// `on_complete` closure; exactly one side takes the token and completes it
/// (§2.3).
pub type TokenSlot = Arc<Mutex<Option<SessionExecutionToken>>>;

/// What admission hands back to `exec_command` while the caller still holds
/// the session admission gate (caller-locks realization of §2.3).
pub struct AdmittedCommand {
    pub handler: WorkspaceSessionHandler,
    pub finalize_policy: FinalizePolicy,
    pub token_slot: TokenSlot,
    pub finalize_outcome: Arc<OnceLock<FinalizeOutcome>>,
}

/// RAII completion for one admitted command: dropping the token removes the
/// command's ledger entry and runs the finalize policy when the ledger
/// drained. Completion against a missing session or ledger entry is a silent
/// no-op; the drop is panic-contained and never poisons the watcher.
pub struct SessionExecutionToken {
    service: Arc<WorkspaceSessionService>,
    workspace_session_id: WorkspaceSessionId,
    command_session_id: NamespaceExecutionId,
    finalize_outcome: Arc<OnceLock<FinalizeOutcome>>,
    completed: bool,
}

impl Drop for SessionExecutionToken {
    fn drop(&mut self) {
        if self.completed {
            return;
        }
        self.completed = true;
        let completion = catch_unwind(AssertUnwindSafe(|| {
            self.service.complete_activity(
                &self.workspace_session_id,
                &self.command_session_id,
                &self.finalize_outcome,
            );
        }));
        if completion.is_err() {
            self.service.obs().event(
                names::WORKSPACE_SESSION_FINALIZE_FAILED,
                json!({
                    "workspace_session_id": self.workspace_session_id.0,
                    "command_session_id": self.command_session_id.0,
                    "panicked": true,
                }),
            );
        }
    }
}

impl WorkspaceSessionService {
    /// Admit one command into the session's ledger. The caller must already
    /// hold `gate`'s guard and keep holding it through transcript prep, launch,
    /// and attach (§2.3); `admission` is the proof-of-lock. A destroyed or
    /// finalizing session fails `not_found` and cleans the gates-map entry the
    /// lookup may have resurrected.
    ///
    /// # Errors
    /// Returns [`WorkspaceSessionError::NotFound`] when the session is absent
    /// or no longer active, or a lock error when session state is unreadable.
    pub fn admit_command_locked(
        self: &Arc<Self>,
        gate: &Arc<Mutex<()>>,
        admission: &MutexGuard<'_, ()>,
        workspace_session_id: &WorkspaceSessionId,
        command_session_id: NamespaceExecutionId,
    ) -> Result<AdmittedCommand, WorkspaceSessionError> {
        let _ = admission;
        let (handler, finalize_policy) = {
            let mut sessions = self.lock_sessions()?;
            let session = sessions
                .get_mut(workspace_session_id)
                .filter(|session| session.finalization_state == FinalizationState::Active);
            let Some(session) = session else {
                drop(sessions);
                self.discard_resurrected_gate(workspace_session_id, gate);
                return Err(WorkspaceSessionError::not_found(workspace_session_id));
            };
            session.active_commands.insert(command_session_id.clone());
            (session.handler(), session.finalize_policy)
        };
        let finalize_outcome = Arc::new(OnceLock::new());
        let token = SessionExecutionToken {
            service: Arc::clone(self),
            workspace_session_id: workspace_session_id.clone(),
            command_session_id,
            finalize_outcome: Arc::clone(&finalize_outcome),
            completed: false,
        };
        Ok(AdmittedCommand {
            handler,
            finalize_policy,
            token_slot: Arc::new(Mutex::new(Some(token))),
            finalize_outcome,
        })
    }

    /// Resolve the session inside its admission gate and run `f` on the fresh
    /// handler while the gate is held. Synchronous file ops and remounts route
    /// through here: no ledger mutation, no finalization (§2.3 / F1), and no
    /// pre-gate stale handler.
    ///
    /// # Errors
    /// Returns [`WorkspaceSessionError::NotFound`] when the session is absent
    /// or no longer active, or a lock error when session state is unreadable.
    pub fn with_gated_session<R>(
        &self,
        workspace_session_id: &WorkspaceSessionId,
        f: impl FnOnce(&WorkspaceSessionHandler) -> R,
    ) -> Result<R, WorkspaceSessionError> {
        let gate = self.session_gate(workspace_session_id);
        let _admission = gate.lock().unwrap_or_else(PoisonError::into_inner);
        let handler = {
            let sessions = self.lock_sessions()?;
            let session = sessions
                .get(workspace_session_id)
                .filter(|session| session.finalization_state == FinalizationState::Active);
            match session {
                Some(session) => session.handler(),
                None => {
                    drop(sessions);
                    self.discard_resurrected_gate(workspace_session_id, &gate);
                    return Err(WorkspaceSessionError::not_found(workspace_session_id));
                }
            }
        };
        Ok(f(&handler))
    }

    /// Command-completion edge (token drop): locks the gate itself on the
    /// calling thread, removes the ledger entry, and runs the finalize policy
    /// when the ledger drained. A missing session or ledger entry is a silent
    /// no-op (§2.3 / F5).
    pub(crate) fn complete_activity(
        &self,
        workspace_session_id: &WorkspaceSessionId,
        command_session_id: &NamespaceExecutionId,
        finalize_outcome: &Arc<OnceLock<FinalizeOutcome>>,
    ) {
        let gate = self.session_gate(workspace_session_id);
        let _admission = gate.lock().unwrap_or_else(PoisonError::into_inner);
        self.complete_under_gate(
            workspace_session_id,
            command_session_id,
            finalize_outcome,
            Some(&gate),
        );
    }

    /// Completion for failure paths that already hold the admission guard
    /// (§2.3): consumes the token without re-locking the gate. The token's own
    /// drop is defused first, so unwinding cannot double-complete.
    pub(crate) fn complete_admitted_locked(
        &self,
        mut token: SessionExecutionToken,
        admission: &MutexGuard<'_, ()>,
    ) {
        let _ = admission;
        token.completed = true;
        let workspace_session_id = token.workspace_session_id.clone();
        let command_session_id = token.command_session_id.clone();
        let finalize_outcome = Arc::clone(&token.finalize_outcome);
        drop(token);
        self.complete_under_gate(
            &workspace_session_id,
            &command_session_id,
            &finalize_outcome,
            None,
        );
    }

    fn complete_under_gate(
        &self,
        workspace_session_id: &WorkspaceSessionId,
        command_session_id: &NamespaceExecutionId,
        finalize_outcome: &Arc<OnceLock<FinalizeOutcome>>,
        resurrected_gate: Option<&Arc<Mutex<()>>>,
    ) {
        let snapshot = {
            let Ok(mut sessions) = self.lock_sessions() else {
                self.obs().event(
                    names::WORKSPACE_SESSION_FINALIZE_FAILED,
                    json!({
                        "workspace_session_id": workspace_session_id.0,
                        "command_session_id": command_session_id.0,
                        "error": "sessions lock poisoned during completion",
                    }),
                );
                return;
            };
            let Some(session) = sessions.get_mut(workspace_session_id) else {
                drop(sessions);
                if let Some(gate) = resurrected_gate {
                    self.discard_resurrected_gate(workspace_session_id, gate);
                }
                return;
            };
            if !session.active_commands.remove(command_session_id) {
                return;
            }
            if !session.active_commands.is_empty()
                || session.finalize_policy != FinalizePolicy::PublishThenDestroy
                || session.finalization_state != FinalizationState::Active
            {
                return;
            }
            session.finalization_state = FinalizationState::Finalizing;
            session.handler()
        };
        self.finalize_session_snapshot(snapshot, finalize_outcome);
    }
}
