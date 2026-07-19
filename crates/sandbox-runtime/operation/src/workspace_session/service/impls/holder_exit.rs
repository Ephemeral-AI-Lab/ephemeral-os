use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::{Arc, PoisonError};
use std::time::Instant;

use crate::workspace_crate::{DestroyWorkspaceRequest, DestroyWorkspaceResult};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

use super::super::core::DestroyFlight;
use super::super::model::{
    FinalizePolicy, HolderExitDisposition, HolderExitOutcome, HolderLifecycleEventKind,
    HolderLifecycleSnapshot, WorkspaceSessionHandler,
};

impl WorkspaceSessionService {
    /// Reconcile every holder exit observed by the workspace supervisor.
    /// There is deliberately no idle timer: admission sees the shared liveness
    /// flag immediately, while the holder supervisor invokes this bounded pass.
    #[must_use]
    pub fn reconcile_holder_exits(&self) -> Vec<HolderExitOutcome> {
        let _ = self.workspace().reconcile_pending_teardowns();
        let workspace_ids = self
            .lock_sessions()
            .map(|sessions| {
                sessions
                    .values()
                    .filter(|session| self.holder_cleanup_is_pending(session))
                    .map(|session| session.workspace_session_id.clone())
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();

        let mut outcomes = Vec::new();
        for workspace_session_id in workspace_ids {
            if let Ok(Some(flight)) = self.existing_destroy_flight(&workspace_session_id) {
                if flight.holder_plan.is_some() {
                    outcomes.push(Self::wait_holder_destroy_flight(&flight));
                    continue;
                }
            }

            let gate = self.session_gate(&workspace_session_id);
            let admission = gate.lock().unwrap_or_else(PoisonError::into_inner);
            let handler = {
                let Ok(sessions) = self.lock_sessions() else {
                    continue;
                };
                let Some(session) = sessions.get(&workspace_session_id) else {
                    continue;
                };
                if !self.holder_cleanup_is_pending(session) {
                    continue;
                }
                session.handler()
            };
            let claimed = self.claim_holder_destroy_flight(&handler);
            drop(admission);
            let Ok((flight, leader)) = claimed else {
                continue;
            };
            if leader {
                let _ = self
                    .run_holder_destroy(Arc::clone(&flight), DestroyWorkspaceRequest::default());
            }
            outcomes.push(Self::wait_holder_destroy_flight(&flight));
        }
        outcomes
    }

    /// Execute the single dead-holder teardown owner. The immutable plan was
    /// captured atomically with the flight claim, before any lifecycle state
    /// was changed. Commands are cancelled and joined without the admission
    /// gate; the gate is then reacquired for ledger verification, recovery,
    /// and raw teardown.
    pub(in crate::workspace_session::service::impls) fn run_holder_destroy(
        &self,
        flight: Arc<DestroyFlight>,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceSessionError> {
        let plan = flight
            .holder_plan
            .as_ref()
            .expect("holder leader owns an immutable holder plan")
            .clone();
        let cleanup_started = Instant::now();
        if plan.newly_observed {
            self.record_holder_lifecycle(
                plan.handler.workspace_session_id.clone(),
                HolderLifecycleEventKind::ExitObserved,
                plan.reason.clone(),
                None,
            );
        }
        self.record_holder_lifecycle(
            plan.handler.workspace_session_id.clone(),
            HolderLifecycleEventKind::CleanupAttempt,
            format!("destroy-attempt:{}", plan.attempt),
            None,
        );

        let execution = catch_unwind(AssertUnwindSafe(|| {
            self.cancel_and_join_commands(&plan.handler.workspace_session_id, &plan.command_ids)
                .map_err(|error| holder_failure(&plan.handler, error))?;

            let gate = self.session_gate(&plan.handler.workspace_session_id);
            let _admission = gate.lock().unwrap_or_else(PoisonError::into_inner);
            {
                let sessions = self.lock_sessions()?;
                let session = sessions
                    .get(&plan.handler.workspace_session_id)
                    .ok_or_else(|| {
                        WorkspaceSessionError::not_found(&plan.handler.workspace_session_id)
                    })?;
                if session.handler() != plan.handler {
                    return Err(WorkspaceSessionError::not_found(
                        &plan.handler.workspace_session_id,
                    ));
                }
                if !session.active_commands.is_empty() {
                    let remaining = session
                        .active_commands
                        .iter()
                        .map(|id| id.0.as_str())
                        .collect::<Vec<_>>()
                        .join(",");
                    return Err(holder_failure(
                        &plan.handler,
                        format!("command ledger remained active after join: {remaining}"),
                    ));
                }
            }

            let artifact = if plan.policy == FinalizePolicy::PublishThenDestroy {
                Some(
                    self.preserve_holder_recovery_artifact(
                        &plan.handler.workspace_session_id,
                        &plan.handler,
                    )
                    .map_err(|error| holder_failure(&plan.handler, error))?,
                )
            } else {
                None
            };
            let result = self.execute_destroy_transaction(&plan.handler, request)?;
            Ok((result, artifact))
        }))
        .unwrap_or_else(|_| {
            Err(holder_failure(
                &plan.handler,
                "holder teardown transaction panicked".to_owned(),
            ))
        });

        let (result, disposition) = match execution {
            Ok((result, artifact)) => {
                let disposition = match artifact {
                    Some(artifact) => HolderExitDisposition::RecoveryRequired { artifact },
                    None => HolderExitDisposition::Destroyed,
                };
                self.record_holder_lifecycle(
                    plan.handler.workspace_session_id.clone(),
                    HolderLifecycleEventKind::CleanupTerminal,
                    if plan.policy == FinalizePolicy::PublishThenDestroy {
                        "recovery-required".to_owned()
                    } else {
                        "destroyed".to_owned()
                    },
                    Some(elapsed_millis(cleanup_started)),
                );
                (Ok(result), disposition)
            }
            Err(error) => {
                self.record_holder_cleanup_failure(
                    &plan.handler,
                    plan.attempt,
                    &error.to_string(),
                    cleanup_started,
                );
                let disposition = HolderExitDisposition::RetryableCleanupFailure {
                    diagnostic: error.to_string(),
                };
                (Err(error), disposition)
            }
        };

        self.publish_destroy_flight(&plan.handler, &flight, result, Some(disposition))
    }

    pub(in crate::workspace_session::service::impls) fn wait_holder_destroy_flight(
        flight: &Arc<DestroyFlight>,
    ) -> HolderExitOutcome {
        let plan = flight
            .holder_plan
            .as_ref()
            .expect("holder flight always carries its immutable plan");
        let terminal = Self::wait_destroy_terminal(flight);
        let disposition = match terminal {
            Ok(terminal) => terminal
                .holder_disposition
                .unwrap_or_else(|| match terminal.result {
                    Ok(_) => HolderExitDisposition::Destroyed,
                    Err(error) => HolderExitDisposition::RetryableCleanupFailure {
                        diagnostic: error.to_string(),
                    },
                }),
            Err(error) => HolderExitDisposition::RetryableCleanupFailure {
                diagnostic: error.to_string(),
            },
        };
        HolderExitOutcome {
            workspace_session_id: plan.handler.workspace_session_id.clone(),
            reason: plan.reason.clone(),
            disposition,
        }
    }

    pub(in crate::workspace_session::service::impls) fn record_holder_cleanup_failure(
        &self,
        handler: &WorkspaceSessionHandler,
        attempt: u8,
        diagnostic: &str,
        cleanup_started: Instant,
    ) {
        self.record_holder_lifecycle(
            handler.workspace_session_id.clone(),
            HolderLifecycleEventKind::CleanupFailure,
            format!("destroy-attempt:{attempt}: {diagnostic}"),
            Some(elapsed_millis(cleanup_started)),
        );
        self.mark_destroy_failed(handler);
    }

    pub(in crate::workspace_session::service::impls) fn preserve_holder_recovery_artifact(
        &self,
        workspace_session_id: &crate::workspace_crate::WorkspaceSessionId,
        handler: &WorkspaceSessionHandler,
    ) -> Result<std::path::PathBuf, String> {
        let upperdir = handler
            .handle
            .entry()
            .map_err(|error| format!("resolve recovery source: {error}"))?
            .upperdir;
        let layer_stack_root = self.layerstack().layer_stack_root();
        let recovery_root = layer_stack_root
            .parent()
            .unwrap_or(layer_stack_root)
            .join("storage")
            .join("workspace_recovery");
        let holder_identity = handler.handle.holder_identity();
        super::super::recovery::preserve_recovery_artifact(
            &recovery_root,
            workspace_session_id,
            &holder_identity,
            &upperdir,
        )
    }

    #[must_use]
    pub fn holder_lifecycle_snapshot(&self) -> HolderLifecycleSnapshot {
        self.holder_lifecycle
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .snapshot()
    }

    pub(in crate::workspace_session::service::impls) fn record_holder_lifecycle(
        &self,
        workspace_session_id: crate::workspace_crate::WorkspaceSessionId,
        kind: HolderLifecycleEventKind,
        detail: String,
        cleanup_duration_ms: Option<u64>,
    ) {
        self.holder_lifecycle
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .record(workspace_session_id, kind, detail, cleanup_duration_ms);
    }

    fn holder_cleanup_is_pending(&self, session: &super::super::model::WorkspaceSession) -> bool {
        !self.workspace().holder_is_live(&session.handle)
            && !session.holder_cleanup_terminal
            && (!session.holder_quiesced_for_finalization
                || session.finalization_state
                    == super::super::model::FinalizationState::FinalizeFailed)
    }
}

fn holder_failure(handler: &WorkspaceSessionHandler, error: String) -> WorkspaceSessionError {
    WorkspaceSessionError::FinalizationFailed {
        workspace_session_id: handler.workspace_session_id.clone(),
        error,
    }
}

fn elapsed_millis(started: Instant) -> u64 {
    started.elapsed().as_millis().try_into().unwrap_or(u64::MAX)
}
