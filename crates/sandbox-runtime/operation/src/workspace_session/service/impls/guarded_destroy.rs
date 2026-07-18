use std::sync::PoisonError;
use std::time::Instant;

use crate::workspace_crate::{DestroyWorkspaceRequest, DestroyWorkspaceResult, WorkspaceSessionId};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

use super::super::model::{FinalizationState, FinalizePolicy, HolderLifecycleEventKind};

struct DeadHolderDestroyPlan {
    policy: FinalizePolicy,
    reason: String,
    newly_observed: bool,
    attempt: u8,
}

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
        // A holder owner keeps the admission gate during raw teardown. Check
        // its already-published reservation before waiting for that gate so a
        // concurrent explicit destroy observes the same success or failure
        // instead of silently starting a retry after the owner releases it.
        if let Some(flight) = self.existing_destroy_flight(&workspace_session_id)? {
            return Self::wait_destroy_flight(&flight);
        }
        let admission = gate.lock().unwrap_or_else(PoisonError::into_inner);
        if let Some(flight) = self.existing_destroy_flight(&workspace_session_id)? {
            drop(admission);
            return Self::wait_destroy_flight(&flight);
        }
        let (handler, dead_holder) = {
            let mut sessions = self.lock_sessions()?;
            let Some(session) = sessions.get_mut(&workspace_session_id) else {
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
            let dead_holder = if session.handle.holder_is_live() {
                None
            } else {
                let newly_observed = !session.holder_exit_recorded;
                session.holder_exit_recorded = true;
                session.finalization_state = FinalizationState::Finalizing;
                session.holder_cleanup_attempts = session.holder_cleanup_attempts.saturating_add(1);
                Some(DeadHolderDestroyPlan {
                    policy: session.finalize_policy,
                    reason: session
                        .handle
                        .holder_exit_reason()
                        .unwrap_or_else(|| "exit-status:unknown".to_owned()),
                    newly_observed,
                    attempt: session.holder_cleanup_attempts,
                })
            };
            (session.handler(), dead_holder)
        };
        let (flight, leader) = self.claim_destroy_flight(&workspace_session_id)?;
        if !leader {
            drop(admission);
            return Self::wait_destroy_flight(&flight);
        }
        let cleanup_started = Instant::now();
        if let Some(dead_holder) = &dead_holder {
            if dead_holder.newly_observed {
                self.record_holder_lifecycle(
                    workspace_session_id.clone(),
                    HolderLifecycleEventKind::ExitObserved,
                    dead_holder.reason.clone(),
                    None,
                );
            }
            self.record_holder_lifecycle(
                workspace_session_id.clone(),
                HolderLifecycleEventKind::CleanupAttempt,
                format!("destroy-attempt:{}", dead_holder.attempt),
                None,
            );
            if dead_holder.policy == FinalizePolicy::PublishThenDestroy {
                if let Err(diagnostic) =
                    self.preserve_holder_recovery_artifact(&workspace_session_id, &handler)
                {
                    self.record_holder_cleanup_failure(
                        &workspace_session_id,
                        dead_holder.attempt,
                        &diagnostic,
                        cleanup_started,
                    );
                    let error = WorkspaceSessionError::FinalizationFailed {
                        workspace_session_id: workspace_session_id.clone(),
                        error: diagnostic,
                    };
                    self.fail_claimed_destroy(&workspace_session_id, &flight, error.clone());
                    return Err(error);
                }
            }
        }

        let result =
            self.destroy_claimed_session(handler, DestroyWorkspaceRequest { grace_s }, flight);
        if let Some(dead_holder) = dead_holder {
            match &result {
                Ok(_) => self.record_holder_lifecycle(
                    workspace_session_id.clone(),
                    HolderLifecycleEventKind::CleanupTerminal,
                    if dead_holder.policy == FinalizePolicy::PublishThenDestroy {
                        "recovery-required".to_owned()
                    } else {
                        "destroyed".to_owned()
                    },
                    Some(elapsed_millis(cleanup_started)),
                ),
                Err(error) => self.record_holder_cleanup_failure(
                    &workspace_session_id,
                    dead_holder.attempt,
                    &error.to_string(),
                    cleanup_started,
                ),
            }
        }
        result
    }
}

fn elapsed_millis(started: Instant) -> u64 {
    started.elapsed().as_millis().try_into().unwrap_or(u64::MAX)
}
