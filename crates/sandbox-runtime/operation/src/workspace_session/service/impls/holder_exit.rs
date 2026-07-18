use std::sync::PoisonError;
use std::time::Instant;

use crate::workspace_crate::DestroyWorkspaceRequest;
use crate::workspace_session::WorkspaceSessionService;

use super::super::model::{
    FinalizationState, FinalizePolicy, HolderExitDisposition, HolderExitOutcome,
    HolderLifecycleEventKind, HolderLifecycleSnapshot,
};

impl WorkspaceSessionService {
    /// Reconcile every holder exit observed by the workspace supervisor.
    /// There is deliberately no idle timer: admission sees the shared liveness
    /// flag immediately, while normal revision/snapshot activity invokes this
    /// bounded pass.
    #[must_use]
    pub fn reconcile_holder_exits(&self) -> Vec<HolderExitOutcome> {
        // A raw create can fail after acquiring resources but before the
        // operation session is published. Those transactions have no entry
        // in `sessions`, so join them explicitly on the same event-driven
        // wake used for holder exits. The workspace layer retains failures.
        let _ = self.workspace().reconcile_pending_teardowns();
        let workspace_ids = self
            .lock_sessions()
            .map(|sessions| {
                sessions
                    .values()
                    .filter(|session| holder_cleanup_is_pending(session))
                    .map(|session| session.workspace_session_id.clone())
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();

        let mut outcomes = Vec::new();
        for workspace_session_id in workspace_ids {
            let gate = self.session_gate(&workspace_session_id);
            let initial = {
                let _admission = gate.lock().unwrap_or_else(PoisonError::into_inner);
                let Ok(mut sessions) = self.lock_sessions() else {
                    continue;
                };
                let Some(session) = sessions.get_mut(&workspace_session_id) else {
                    continue;
                };
                if !holder_cleanup_is_pending(session) {
                    continue;
                }
                let reason = session
                    .handle
                    .holder_exit_reason()
                    .unwrap_or_else(|| "exit-status:unknown".to_owned());
                let newly_observed = !session.holder_exit_recorded;
                session.holder_exit_recorded = true;
                session.finalization_state = FinalizationState::Finalizing;
                session.holder_cleanup_attempts = session.holder_cleanup_attempts.saturating_add(1);
                InitialReconcilePlan {
                    handler: session.handler(),
                    policy: session.finalize_policy,
                    command_ids: session.active_commands.iter().cloned().collect(),
                    reason,
                    newly_observed,
                    attempt: session.holder_cleanup_attempts,
                }
            };

            let cleanup_started = Instant::now();
            if initial.newly_observed {
                self.record_holder_lifecycle(
                    workspace_session_id.clone(),
                    HolderLifecycleEventKind::ExitObserved,
                    initial.reason.clone(),
                    None,
                );
            }
            self.record_holder_lifecycle(
                workspace_session_id.clone(),
                HolderLifecycleEventKind::CleanupAttempt,
                format!("destroy-attempt:{}", initial.attempt),
                None,
            );

            if let Err(error) =
                self.cancel_and_join_commands(&workspace_session_id, &initial.command_ids)
            {
                self.record_holder_cleanup_failure(
                    &workspace_session_id,
                    initial.attempt,
                    &error,
                    cleanup_started,
                );
                outcomes.push(HolderExitOutcome {
                    workspace_session_id,
                    reason: initial.reason,
                    disposition: HolderExitDisposition::RetryableCleanupFailure {
                        diagnostic: error,
                    },
                });
                continue;
            }

            // Command completion takes this same gate to retire its ledger
            // token. Reacquiring it after all completion waiters resolve proves
            // that no callback remains in flight before namespace teardown.
            let _admission = gate.lock().unwrap_or_else(PoisonError::into_inner);
            let remaining_commands = self
                .lock_sessions()
                .ok()
                .and_then(|sessions| sessions.get(&workspace_session_id).cloned())
                .map(|session| session.active_commands.into_iter().collect::<Vec<_>>())
                .unwrap_or_default();
            if !remaining_commands.is_empty() {
                let error = format!(
                    "command ledger remained active after join: {}",
                    remaining_commands
                        .iter()
                        .map(|id| id.0.as_str())
                        .collect::<Vec<_>>()
                        .join(",")
                );
                self.record_holder_cleanup_failure(
                    &workspace_session_id,
                    initial.attempt,
                    &error,
                    cleanup_started,
                );
                outcomes.push(HolderExitOutcome {
                    workspace_session_id,
                    reason: initial.reason,
                    disposition: HolderExitDisposition::RetryableCleanupFailure {
                        diagnostic: error,
                    },
                });
                continue;
            }

            match initial.policy {
                FinalizePolicy::NoOp => {
                    match self.destroy_session(initial.handler, DestroyWorkspaceRequest::default())
                    {
                        Ok(_) => {
                            self.record_holder_lifecycle(
                                workspace_session_id.clone(),
                                HolderLifecycleEventKind::CleanupTerminal,
                                "destroyed".to_owned(),
                                Some(elapsed_millis(cleanup_started)),
                            );
                            outcomes.push(HolderExitOutcome {
                                workspace_session_id,
                                reason: initial.reason,
                                disposition: HolderExitDisposition::Destroyed,
                            });
                        }
                        Err(error) => {
                            self.record_holder_cleanup_failure(
                                &workspace_session_id,
                                initial.attempt,
                                &error.to_string(),
                                cleanup_started,
                            );
                            outcomes.push(HolderExitOutcome {
                                workspace_session_id,
                                reason: initial.reason,
                                disposition: HolderExitDisposition::RetryableCleanupFailure {
                                    diagnostic: error.to_string(),
                                },
                            });
                        }
                    }
                }
                FinalizePolicy::PublishThenDestroy => {
                    let upperdir = match initial.handler.handle.entry() {
                        Ok(entry) => entry.upperdir,
                        Err(error) => {
                            let error = format!("resolve recovery source: {error}");
                            self.record_holder_cleanup_failure(
                                &workspace_session_id,
                                initial.attempt,
                                &error,
                                cleanup_started,
                            );
                            outcomes.push(HolderExitOutcome {
                                workspace_session_id,
                                reason: initial.reason,
                                disposition: HolderExitDisposition::RetryableCleanupFailure {
                                    diagnostic: error,
                                },
                            });
                            continue;
                        }
                    };
                    let layer_stack_root = self.layerstack().layer_stack_root();
                    let recovery_root = layer_stack_root
                        .parent()
                        .unwrap_or(layer_stack_root)
                        .join("storage")
                        .join("workspace_recovery");
                    let artifact = match super::super::recovery::preserve_recovery_artifact(
                        &recovery_root,
                        &workspace_session_id,
                        &upperdir,
                    ) {
                        Ok(artifact) => artifact,
                        Err(error) => {
                            self.record_holder_cleanup_failure(
                                &workspace_session_id,
                                initial.attempt,
                                &error,
                                cleanup_started,
                            );
                            outcomes.push(HolderExitOutcome {
                                workspace_session_id,
                                reason: initial.reason,
                                disposition: HolderExitDisposition::RetryableCleanupFailure {
                                    diagnostic: error,
                                },
                            });
                            continue;
                        }
                    };
                    match self.destroy_session(initial.handler, DestroyWorkspaceRequest::default())
                    {
                        Ok(_) => {
                            self.record_holder_lifecycle(
                                workspace_session_id.clone(),
                                HolderLifecycleEventKind::CleanupTerminal,
                                "recovery-required".to_owned(),
                                Some(elapsed_millis(cleanup_started)),
                            );
                            outcomes.push(HolderExitOutcome {
                                workspace_session_id,
                                reason: initial.reason,
                                disposition: HolderExitDisposition::RecoveryRequired { artifact },
                            });
                        }
                        Err(error) => {
                            self.record_holder_cleanup_failure(
                                &workspace_session_id,
                                initial.attempt,
                                &error.to_string(),
                                cleanup_started,
                            );
                            outcomes.push(HolderExitOutcome {
                                workspace_session_id,
                                reason: initial.reason,
                                disposition: HolderExitDisposition::RetryableCleanupFailure {
                                    diagnostic: error.to_string(),
                                },
                            });
                        }
                    }
                }
            }
        }
        outcomes
    }

    fn record_holder_cleanup_failure(
        &self,
        workspace_session_id: &crate::workspace_crate::WorkspaceSessionId,
        attempt: u8,
        diagnostic: &str,
        cleanup_started: Instant,
    ) {
        self.record_holder_lifecycle(
            workspace_session_id.clone(),
            HolderLifecycleEventKind::CleanupFailure,
            format!("destroy-attempt:{attempt}: {diagnostic}"),
            Some(elapsed_millis(cleanup_started)),
        );
        if let Ok(mut sessions) = self.lock_sessions() {
            if let Some(session) = sessions.get_mut(workspace_session_id) {
                session.finalization_state = FinalizationState::FinalizeFailed;
            }
        }
    }

    #[must_use]
    pub fn holder_lifecycle_snapshot(&self) -> HolderLifecycleSnapshot {
        self.holder_lifecycle
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .snapshot()
    }

    #[doc(hidden)]
    #[must_use]
    pub fn finalization_state_for_test(
        &self,
        workspace_session_id: &crate::workspace_crate::WorkspaceSessionId,
    ) -> Option<FinalizationState> {
        self.lock_sessions().ok().and_then(|sessions| {
            sessions
                .get(workspace_session_id)
                .map(|session| session.finalization_state)
        })
    }

    fn record_holder_lifecycle(
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
}

fn elapsed_millis(started: Instant) -> u64 {
    started.elapsed().as_millis().try_into().unwrap_or(u64::MAX)
}

fn holder_cleanup_is_pending(session: &super::super::model::WorkspaceSession) -> bool {
    if session.handle.holder_is_live() || session.holder_cleanup_terminal {
        return false;
    }
    session.finalization_state == FinalizationState::Active
        || (session.finalization_state == FinalizationState::FinalizeFailed
            && session.holder_exit_recorded)
}

struct InitialReconcilePlan {
    handler: super::super::model::WorkspaceSessionHandler,
    policy: FinalizePolicy,
    command_ids: Vec<sandbox_runtime_namespace_execution::NamespaceExecutionId>,
    reason: String,
    newly_observed: bool,
    attempt: u8,
}
