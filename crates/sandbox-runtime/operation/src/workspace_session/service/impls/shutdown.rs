use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::{Arc, OnceLock, PoisonError};

use sandbox_observability_telemetry::record::names;
use serde_json::json;

use crate::workspace_crate::{DestroyWorkspaceRequest, HolderFinalization, WorkspaceSessionId};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

use super::super::model::{FinalizationState, FinalizeOutcome, FinalizePolicy};

const SESSION_CONVERGENCE_ATTEMPTS: usize = 8;

#[derive(Debug, Clone)]
pub(crate) struct WorkspaceSessionShutdownOutcome {
    pub(crate) workspace_session_id: WorkspaceSessionId,
    pub(crate) result: Result<(), String>,
}

impl WorkspaceSessionService {
    pub(crate) fn shutdown_sessions(&self) -> Result<Vec<WorkspaceSessionShutdownOutcome>, String> {
        let workspace_session_ids = self
            .lock_sessions()
            .map(|sessions| {
                let mut ids = sessions.keys().cloned().collect::<Vec<_>>();
                ids.sort_by(|left, right| left.0.cmp(&right.0));
                ids
            })
            .map_err(|error| error.to_string())?;
        Ok(workspace_session_ids
            .into_iter()
            .map(|workspace_session_id| {
                let result = catch_unwind(AssertUnwindSafe(|| {
                    self.shutdown_session(&workspace_session_id)
                }))
                .unwrap_or_else(|_| Err("session shutdown panicked".to_owned()));
                WorkspaceSessionShutdownOutcome {
                    workspace_session_id,
                    result,
                }
            })
            .collect())
    }

    fn shutdown_session(&self, workspace_session_id: &WorkspaceSessionId) -> Result<(), String> {
        for _ in 0..SESSION_CONVERGENCE_ATTEMPTS {
            if let Some(flight) = self
                .existing_destroy_flight(workspace_session_id)
                .map_err(|error| error.to_string())?
            {
                Self::wait_destroy_flight(&flight).map_err(|error| error.to_string())?;
                continue;
            }

            let gate = self.session_gate(workspace_session_id);
            let admission = gate.lock().unwrap_or_else(PoisonError::into_inner);
            if let Some(flight) = self
                .existing_destroy_flight(workspace_session_id)
                .map_err(|error| error.to_string())?
            {
                drop(admission);
                Self::wait_destroy_flight(&flight).map_err(|error| error.to_string())?;
                continue;
            }

            let (handler, holder_is_live, active_commands, finalize_policy, finalization_state) = {
                let sessions = self.lock_sessions().map_err(|error| error.to_string())?;
                let Some(session) = sessions.get(workspace_session_id) else {
                    drop(sessions);
                    drop(admission);
                    self.discard_resurrected_gate(workspace_session_id, &gate);
                    return Ok(());
                };
                (
                    session.handler(),
                    self.workspace().holder_is_live(&session.handle),
                    session.active_commands.iter().cloned().collect::<Vec<_>>(),
                    session.finalize_policy,
                    session.finalization_state,
                )
            };

            if !holder_is_live {
                let claimed = self
                    .claim_holder_destroy_flight(&handler)
                    .map_err(|error| error.to_string())?;
                drop(admission);
                let (flight, leader) = claimed;
                if leader {
                    self.run_holder_destroy(flight, DestroyWorkspaceRequest::default())
                        .map_err(|error| error.to_string())?;
                } else {
                    Self::wait_destroy_flight(&flight).map_err(|error| error.to_string())?;
                }
                return Ok(());
            }

            if !active_commands.is_empty() {
                drop(admission);
                self.cancel_and_join_commands(workspace_session_id, &active_commands)?;
                continue;
            }

            if finalize_policy == FinalizePolicy::PublishThenDestroy
                && finalization_state == FinalizationState::Active
            {
                {
                    let mut sessions = self.lock_sessions().map_err(|error| error.to_string())?;
                    let Some(session) = sessions.get_mut(workspace_session_id) else {
                        drop(sessions);
                        drop(admission);
                        self.discard_resurrected_gate(workspace_session_id, &gate);
                        return Ok(());
                    };
                    if session.handler() != handler {
                        return Err(
                            WorkspaceSessionError::not_found(workspace_session_id).to_string()
                        );
                    }
                    session.finalization_state = FinalizationState::Finalizing;
                }
                let (finalization, attempts) = self.quiesce_session_holder(&handler);
                match finalization {
                    HolderFinalization::Quiesced { proof } => {
                        if let Err(error) = self.mark_holder_quiesced_for_finalization(&handler) {
                            self.mark_destroy_failed(&handler);
                            drop(admission);
                            return Err(error.to_string());
                        }
                        self.finalize_session_snapshot(
                            handler.clone(),
                            &proof,
                            attempts,
                            &Arc::new(OnceLock::<FinalizeOutcome>::new()),
                        );
                    }
                    HolderFinalization::Exited => {
                        let (flight, leader) = self
                            .claim_holder_destroy_flight(&handler)
                            .map_err(|error| error.to_string())?;
                        drop(admission);
                        if leader {
                            self.run_holder_destroy(flight, DestroyWorkspaceRequest::default())
                                .map_err(|error| error.to_string())?;
                        } else {
                            Self::wait_destroy_flight(&flight)
                                .map_err(|error| error.to_string())?;
                        }
                        return Ok(());
                    }
                    HolderFinalization::Unknown { class } => {
                        self.mark_destroy_failed(&handler);
                        self.obs().event(
                            names::WORKSPACE_SESSION_FINALIZE_FAILED,
                            json!({
                                "workspace_session_id": handler.workspace_session_id.0,
                                "stage": "shutdown_holder_finalization",
                                "class": class.as_str(),
                                "attempts": attempts,
                            }),
                        );
                        drop(admission);
                        return Err(format!(
                            "holder finalization failed after {attempts} attempts: {}",
                            class.as_str()
                        ));
                    }
                }
                let retained_state = self
                    .lock_sessions()
                    .map_err(|error| error.to_string())?
                    .get(workspace_session_id)
                    .filter(|session| session.handler() == handler)
                    .map(|session| session.finalization_state);
                drop(admission);
                return match retained_state {
                    None => Ok(()),
                    Some(state) => Err(format!(
                        "publish finalization retained the session in {}",
                        state.as_str()
                    )),
                };
            }

            let (flight, leader) = self
                .claim_destroy_flight(&handler)
                .map_err(|error| error.to_string())?;
            if leader {
                self.destroy_claimed_session(handler, DestroyWorkspaceRequest::default(), flight)
                    .map_err(|error| error.to_string())?;
            } else {
                drop(admission);
                Self::wait_destroy_flight(&flight).map_err(|error| error.to_string())?;
            }
            return Ok(());
        }
        Err(format!(
            "session did not converge after {SESSION_CONVERGENCE_ATTEMPTS} state transitions"
        ))
    }
}
