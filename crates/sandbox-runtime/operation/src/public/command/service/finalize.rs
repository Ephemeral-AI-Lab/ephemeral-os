use std::time::Instant;

use crate::command::{
    CommandFinalizedMetadata, CommandLifecycleState, CommandPublishFinalization,
    CommandPublishStatus, CommandServiceError, CommandSessionId, CommandStatus,
    CommandTerminalResult, CommandTranscriptStore, CompletedCommandRecord, FinalizationState,
    RetainedCommandTranscript,
};
use crate::layerstack::{LayerStackRevision, PublishChangesRequest, PublishChangesResult};
use crate::workspace_crate::{
    BaseRevision, CaptureChangesRequest, ProtectedPathDropReason, RemountWorkspaceRequest,
    WorkspaceSessionId,
};

use super::CommandOperationService;

#[derive(Debug, Clone)]
pub(crate) struct ActiveFinalizationRecord {
    command_session_id: CommandSessionId,
    workspace_session_id: WorkspaceSessionId,
    transcript: CommandTranscriptStore,
}

impl CommandOperationService {
    pub(crate) fn finalize_command(
        &self,
        command_session_id: CommandSessionId,
        process_exit: ::sandbox_runtime_command::process::CommandProcessExit,
    ) -> Result<CommandTerminalResult, CommandServiceError> {
        let record = self.begin_finalization(&command_session_id)?;
        let result = terminal_result(&process_exit);
        let finalized = self.finalize_session_command(&record, &process_exit);

        let finalized = match finalized {
            Ok(finalized) => finalized,
            Err(error) => {
                let retained = rejected_publish_metadata(&error);
                return self.fail_finalization(&command_session_id, error.to_string(), retained);
            }
        };

        match self.complete_finalized_command(record, result.clone(), finalized) {
            Ok(()) => Ok(result),
            Err(error) => self.fail_finalization(&command_session_id, error.to_string(), None),
        }
    }

    fn finalize_session_command(
        &self,
        record: &ActiveFinalizationRecord,
        process_exit: &::sandbox_runtime_command::process::CommandProcessExit,
    ) -> Result<CommandFinalizedMetadata, CommandServiceError> {
        if !process_exit_succeeded(process_exit) {
            return Ok(CommandFinalizedMetadata {
                publish: Some(CommandPublishFinalization {
                    status: CommandPublishStatus::Skipped,
                    rejection: None,
                    revision: None,
                    layer_paths: Vec::new(),
                }),
            });
        }
        let Some(layerstack) = self.layerstack() else {
            return Ok(CommandFinalizedMetadata {
                publish: Some(CommandPublishFinalization {
                    status: CommandPublishStatus::Skipped,
                    rejection: None,
                    revision: None,
                    layer_paths: Vec::new(),
                }),
            });
        };

        let handler = self
            .workspace()
            .resolve_session(record.workspace_session_id.clone())?;
        let captured = self.workspace().capture_session_changes(
            &handler,
            CaptureChangesRequest {
                include_stats: false,
            },
        )?;
        let request = PublishChangesRequest {
            expected_base: LayerStackRevision {
                manifest_version: captured.base_revision.version,
                root_hash: captured.base_revision.root_hash.clone(),
                layer_count: captured.base_revision.layer_count,
            },
            base_manifest: captured.base_manifest,
            protected_drops: captured
                .protected_drops
                .into_iter()
                .map(layer_protected_drop)
                .collect(),
            changes: captured.changes,
        };
        let published = layerstack.publish_changes(request)?;
        let remount_handler = self
            .workspace()
            .begin_remount(record.workspace_session_id.clone())?;
        self.workspace().apply_and_finish_remount(
            &remount_handler,
            RemountWorkspaceRequest {
                layer_paths: published.layer_paths.clone(),
            },
        )?;
        self.workspace().refresh_after_publish(
            record.workspace_session_id.clone(),
            base_revision_from_layerstack(&published.revision),
            published.manifest.clone(),
            published.layer_paths.clone(),
        )?;
        Ok(CommandFinalizedMetadata {
            publish: Some(command_publish_finalization(published)),
        })
    }

    fn begin_finalization(
        &self,
        command_session_id: &CommandSessionId,
    ) -> Result<ActiveFinalizationRecord, CommandServiceError> {
        let active = self
            .process_store()
            .active(command_session_id)
            .ok_or_else(|| CommandServiceError::CommandNotFound {
                command_session_id: command_session_id.clone(),
            })?;
        if let FinalizationState::Failed { error, finalized } = &active.finalization {
            return Err(CommandServiceError::CommandFinalizationFailed {
                command_session_id: command_session_id.clone(),
                error: error.clone(),
                finalized: finalized.clone(),
            });
        }
        let record = ActiveFinalizationRecord {
            command_session_id: active.command_session_id.clone(),
            workspace_session_id: active.workspace_session_id.clone(),
            transcript: active.transcript.clone(),
        };
        drop(active);

        self.mark_active_finalization(
            command_session_id,
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
        let command_session_id = record.command_session_id.clone();
        let completed = CompletedCommandRecord {
            command_session_id: command_session_id.clone(),
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
        command_session_id: &CommandSessionId,
        lifecycle_state: CommandLifecycleState,
        finalization: FinalizationState,
    ) -> Result<(), CommandServiceError> {
        self.process_store()
            .update_active(command_session_id, |active| {
                active.lifecycle_state = lifecycle_state;
                active.finalization = finalization;
            })
            .ok_or_else(|| CommandServiceError::CommandNotFound {
                command_session_id: command_session_id.clone(),
            })
    }

    fn fail_finalization<T>(
        &self,
        command_session_id: &CommandSessionId,
        error: String,
        finalized_override: Option<CommandFinalizedMetadata>,
    ) -> Result<T, CommandServiceError> {
        let finalized = self
            .process_store()
            .update_active(command_session_id, |active| {
                let finalized = finalized_override
                    .clone()
                    .or_else(|| retained_finalized_metadata(&active.finalization));
                active.lifecycle_state = CommandLifecycleState::FinalizationFailed;
                active.finalization = FinalizationState::Failed {
                    error: error.clone(),
                    finalized: finalized.clone().map(Box::new),
                };
                finalized
            });
        Err(CommandServiceError::CommandFinalizationFailed {
            command_session_id: command_session_id.clone(),
            error,
            finalized: finalized.flatten().map(Box::new),
        })
    }
}

fn layer_protected_drop(
    drop: crate::workspace_crate::ProtectedPathDrop,
) -> sandbox_runtime_layerstack::LayerProtectedDrop {
    sandbox_runtime_layerstack::LayerProtectedDrop {
        path: drop.path,
        reason: match drop.reason {
            ProtectedPathDropReason::UnsupportedSpecialFile => {
                sandbox_runtime_layerstack::LayerProtectedDropReason::UnsupportedSpecialFile
            }
            ProtectedPathDropReason::InvalidLayerPath => {
                sandbox_runtime_layerstack::LayerProtectedDropReason::InvalidLayerPath
            }
        },
    }
}

fn base_revision_from_layerstack(revision: &LayerStackRevision) -> BaseRevision {
    BaseRevision {
        version: revision.manifest_version,
        root_hash: revision.root_hash.clone(),
        layer_count: revision.layer_count,
    }
}

fn command_publish_finalization(published: PublishChangesResult) -> CommandPublishFinalization {
    CommandPublishFinalization {
        status: if published.no_op {
            CommandPublishStatus::NoOp
        } else {
            CommandPublishStatus::Published
        },
        rejection: None,
        revision: Some(published.revision),
        layer_paths: published.layer_paths,
    }
}

fn rejected_publish_metadata(error: &CommandServiceError) -> Option<CommandFinalizedMetadata> {
    let CommandServiceError::LayerStack(error) = error else {
        return None;
    };
    let crate::layerstack::LayerStackServiceError::PublishRejected { rejection } = error.as_ref()
    else {
        return None;
    };
    Some(CommandFinalizedMetadata {
        publish: Some(CommandPublishFinalization {
            status: CommandPublishStatus::Rejected,
            rejection: Some(rejection.clone()),
            revision: None,
            layer_paths: Vec::new(),
        }),
    })
}

fn terminal_result(
    process_exit: &::sandbox_runtime_command::process::CommandProcessExit,
) -> CommandTerminalResult {
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

fn process_exit_succeeded(
    process_exit: &::sandbox_runtime_command::process::CommandProcessExit,
) -> bool {
    process_exit.kill.is_none() && process_exit.exit_code == 0
}

fn retained_finalized_metadata(state: &FinalizationState) -> Option<CommandFinalizedMetadata> {
    match state {
        FinalizationState::Failed { finalized, .. } => {
            finalized.as_ref().map(|metadata| metadata.as_ref().clone())
        }
        FinalizationState::NotStarted
        | FinalizationState::InProgress
        | FinalizationState::Complete => None,
    }
}
