use std::time::Instant;

use crate::command::{
    CommandFinalizationOutcome, CommandFinalizePolicy, CommandFinalizedMetadata,
    CommandFinalizedPolicy, CommandId, CommandLifecycleState, CommandServiceError, CommandStatus,
    CommandTerminalResult, CommandTranscriptStore, CommandWorkspaceDestroyMetadata,
    CompletedCommandRecord, FinalizationState, RetainedCommandTranscript,
};
use crate::workspace_crate::{CallerId, DestroyWorkspaceResult, WorkspaceId};
use crate::workspace_session::{OneShotSessionFinalization, PublishedSessionChanges};

use super::service::CommandOperationService;

#[derive(Debug, Clone)]
pub(crate) struct ActiveFinalizationRecord {
    command_id: CommandId,
    caller_id: CallerId,
    workspace_session_id: WorkspaceId,
    transcript: CommandTranscriptStore,
    finalize_policy: CommandFinalizePolicy,
}

impl CommandOperationService {
    pub(crate) fn finalize_command(
        &self,
        command_id: CommandId,
        process_exit: ::command::process::CommandProcessExit,
    ) -> Result<CommandTerminalResult, CommandServiceError> {
        let record = self.begin_finalization(&command_id)?;
        let result = terminal_result(&process_exit);
        let finalized = match record.finalize_policy.clone() {
            CommandFinalizePolicy::Session { .. } => {
                self.finalize_session_command(&record, &process_exit)
            }
            CommandFinalizePolicy::OneShotPublishThenDestroy { .. } => {
                self.finalize_one_shot_command(&record, &process_exit)
            }
        };

        let finalized = match finalized {
            Ok(finalized) => finalized,
            Err(error) => {
                return self.fail_finalization(&command_id, error.to_string());
            }
        };

        match self.complete_finalized_command(record, result.clone(), finalized) {
            Ok(()) => Ok(result),
            Err(error) => self.fail_finalization(&command_id, error.to_string()),
        }
    }

    fn finalize_session_command(
        &self,
        _record: &ActiveFinalizationRecord,
        _process_exit: &::command::process::CommandProcessExit,
    ) -> Result<CommandFinalizedMetadata, CommandServiceError> {
        Ok(CommandFinalizedMetadata {
            policy: CommandFinalizedPolicy::Session,
            outcome: CommandFinalizationOutcome::SessionComplete,
            ..CommandFinalizedMetadata::default()
        })
    }

    fn finalize_one_shot_command(
        &self,
        record: &ActiveFinalizationRecord,
        process_exit: &::command::process::CommandProcessExit,
    ) -> Result<CommandFinalizedMetadata, CommandServiceError> {
        let handler = self.workspace().resolve_session(
            record.workspace_session_id.clone(),
            record.caller_id.clone(),
        )?;
        let finalized = metadata_from_one_shot_finalization(
            self.workspace()
                .finalize_one_shot_session(handler, process_exit_succeeded(process_exit))?,
        );

        self.mark_active_finalization(
            &record.command_id,
            CommandLifecycleState::Finalizing,
            FinalizationState::ResponseBuffered {
                finalized: finalized.clone(),
            },
        )?;
        self.mark_active_finalization(
            &record.command_id,
            CommandLifecycleState::DestroyPending,
            FinalizationState::WorkspaceDestroyPending {
                finalized: finalized.clone(),
            },
        )?;
        Ok(finalized)
    }
    fn begin_finalization(
        &self,
        command_id: &CommandId,
    ) -> Result<ActiveFinalizationRecord, CommandServiceError> {
        let active = self.process_store().active(command_id).ok_or_else(|| {
            CommandServiceError::CommandNotFound {
                command_id: command_id.clone(),
            }
        })?;
        if let FinalizationState::Failed { error, finalized } = &active.finalization {
            return Err(CommandServiceError::CommandFinalizationFailed {
                command_id: command_id.clone(),
                error: error.clone(),
                finalized: finalized.clone().map(Box::new),
            });
        }
        let record = ActiveFinalizationRecord {
            command_id: active.command_id.clone(),
            caller_id: active.caller_id.clone(),
            workspace_session_id: active.workspace_session_id.clone(),
            transcript: active.transcript.clone(),
            finalize_policy: active.finalize_policy.clone(),
        };
        drop(active);

        self.mark_active_finalization(
            command_id,
            CommandLifecycleState::Finalizing,
            FinalizationState::InProgress,
        )?;
        Ok(record)
    }

    fn complete_finalized_command(
        &self,
        record: ActiveFinalizationRecord,
        result: CommandTerminalResult,
        finalized: CommandFinalizedMetadata,
    ) -> Result<(), CommandServiceError> {
        let command_id = record.command_id.clone();
        let completed = CompletedCommandRecord {
            command_id: command_id.clone(),
            caller_id: record.caller_id,
            workspace_session_id: record.workspace_session_id,
            result,
            transcript: RetainedCommandTranscript {
                transcript_path: record.transcript.transcript_path,
            },
            finalization: FinalizationState::Complete,
            finalized: Some(finalized),
            completed_at: Instant::now(),
        };
        let _ = self.process_store().complete_active(completed)?;
        Ok(())
    }

    fn mark_active_finalization(
        &self,
        command_id: &CommandId,
        lifecycle_state: CommandLifecycleState,
        finalization: FinalizationState,
    ) -> Result<(), CommandServiceError> {
        self.process_store()
            .update_active(command_id, |active| {
                active.lifecycle_state = lifecycle_state;
                active.finalization = finalization;
            })
            .ok_or_else(|| CommandServiceError::CommandNotFound {
                command_id: command_id.clone(),
            })
    }

    fn fail_finalization<T>(
        &self,
        command_id: &CommandId,
        error: String,
    ) -> Result<T, CommandServiceError> {
        let finalized = self.process_store().update_active(command_id, |active| {
            let finalized = retained_finalized_metadata(&active.finalization);
            active.lifecycle_state = CommandLifecycleState::FinalizationFailed;
            active.finalization = FinalizationState::Failed {
                error: error.clone(),
                finalized: finalized.clone(),
            };
            finalized
        });
        Err(CommandServiceError::CommandFinalizationFailed {
            command_id: command_id.clone(),
            error,
            finalized: finalized.flatten().map(Box::new),
        })
    }
}

fn terminal_result(process_exit: &::command::process::CommandProcessExit) -> CommandTerminalResult {
    CommandTerminalResult {
        status: if process_exit_succeeded(process_exit) {
            CommandStatus::Completed
        } else {
            CommandStatus::Failed
        },
        exit_code: Some(process_exit.exit_code),
        stdout: process_exit.stdout.clone(),
    }
}

fn process_exit_succeeded(process_exit: &::command::process::CommandProcessExit) -> bool {
    process_exit.kill.is_none() && process_exit.exit_code == 0
}

fn metadata_from_one_shot_finalization(
    finalization: OneShotSessionFinalization,
) -> CommandFinalizedMetadata {
    let mut finalized = match finalization.published {
        Some(published) => metadata_from_published_session(published),
        None => CommandFinalizedMetadata {
            policy: CommandFinalizedPolicy::OneShotPublishThenDestroy,
            outcome: CommandFinalizationOutcome::Discarded,
            ..CommandFinalizedMetadata::default()
        },
    };
    finalized.destroy = Some(destroy_metadata(finalization.destroy));
    finalized
}

fn metadata_from_published_session(published: PublishedSessionChanges) -> CommandFinalizedMetadata {
    CommandFinalizedMetadata {
        policy: CommandFinalizedPolicy::OneShotPublishThenDestroy,
        outcome: CommandFinalizationOutcome::Published,
        changed_paths: published.changed_paths,
        changed_path_kinds: published.changed_path_kinds,
        protected_drop_count: published.protected_drop_count,
        captured_change_count: published.captured_change_count,
        metadata_path_count: published.metadata_path_count,
        published_manifest_version: published.published_manifest_version,
        destroy: None,
    }
}

fn destroy_metadata(result: DestroyWorkspaceResult) -> CommandWorkspaceDestroyMetadata {
    CommandWorkspaceDestroyMetadata {
        evicted_upperdir_bytes: result.evicted_upperdir_bytes,
        lease_released: result.lease_released,
        lease_release_error: result.lease_release_error,
        active_leases_after: result.active_leases_after,
    }
}

fn retained_finalized_metadata(state: &FinalizationState) -> Option<CommandFinalizedMetadata> {
    match state {
        FinalizationState::ResponseBuffered { finalized }
        | FinalizationState::WorkspaceDestroyPending { finalized } => Some(finalized.clone()),
        FinalizationState::Failed { finalized, .. } => finalized.clone(),
        FinalizationState::NotStarted
        | FinalizationState::InProgress
        | FinalizationState::Complete => None,
    }
}
