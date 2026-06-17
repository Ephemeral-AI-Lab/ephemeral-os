use std::sync::Arc;
use std::time::Instant;

use crate::command::{
    CancelCommandInput, CancellationState, CommandCallContext, CommandLifecycleState,
    CommandLinesOutput, CommandOutputLine, CommandOutputSnapshot, CommandPollOutput,
    CommandProcessStore, CommandRegistry, CommandServiceError, CommandStatus, CommandYield,
    CompletedCommandRecord, PollCommandInput, ReadCommandLinesInput, WriteStdinInput,
};
use crate::workspace_crate::CallerId;
use crate::workspace_manager::WorkspaceManagerService;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct CommandFinalizationOptions {
    pub one_shot_capture: layerstack::service::BoundedCaptureOptions,
    pub one_shot_publish: layerstack::CommitOptions,
}

pub struct CommandOperationService {
    workspace: Arc<WorkspaceManagerService>,
    config: ::command::CommandConfig,
    registry: Arc<CommandRegistry>,
    process_store: Arc<CommandProcessStore>,
    finalization_options: CommandFinalizationOptions,
}

impl CommandOperationService {
    #[must_use]
    pub fn new(workspace: Arc<WorkspaceManagerService>, config: ::command::CommandConfig) -> Self {
        Self::with_finalization_options(workspace, config, CommandFinalizationOptions::default())
    }

    #[must_use]
    pub fn with_finalization_options(
        workspace: Arc<WorkspaceManagerService>,
        config: ::command::CommandConfig,
        finalization_options: CommandFinalizationOptions,
    ) -> Self {
        Self {
            workspace,
            config,
            registry: Arc::new(CommandRegistry::new()),
            process_store: Arc::new(CommandProcessStore::new()),
            finalization_options,
        }
    }

    #[must_use]
    pub fn finalization_options(&self) -> &CommandFinalizationOptions {
        &self.finalization_options
    }

    #[must_use]
    pub fn workspace(&self) -> &Arc<WorkspaceManagerService> {
        &self.workspace
    }

    #[must_use]
    pub fn config(&self) -> &::command::CommandConfig {
        &self.config
    }

    #[must_use]
    pub fn registry(&self) -> &Arc<CommandRegistry> {
        &self.registry
    }

    #[must_use]
    pub fn process_store(&self) -> &Arc<CommandProcessStore> {
        &self.process_store
    }

    pub fn write_stdin(
        &self,
        input: WriteStdinInput,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError> {
        let command_id = input.command_id;
        let yield_time_ms = input
            .yield_time_ms
            .unwrap_or(self.config.default_yield_time_ms);
        let output = {
            let active = self.active_for_owner(&command_id, &context.caller_id)?;
            active
                .process
                .write_process_stdin(&input.chars)
                .map_err(|error| CommandServiceError::CommandIo {
                    command_id: command_id.clone(),
                    error: error.to_string(),
                })?;
            if yield_time_ms == 0 {
                String::new()
            } else {
                active.process.read_output_since(0)
            }
        };

        Ok(CommandYield {
            command_id: Some(command_id),
            status: CommandStatus::Running,
            exit_code: None,
            output: CommandOutputSnapshot { stdout: output },
            finalized: None,
        })
    }

    pub fn read_lines(
        &self,
        input: ReadCommandLinesInput,
        context: CommandCallContext,
    ) -> Result<CommandLinesOutput, CommandServiceError> {
        let command_id = input.command_id;
        if let Some(active) = self.active_for_owner_or_none(&command_id, &context.caller_id)? {
            return Ok(line_window(
                command_id,
                &active.process.read_output_since(0),
                input.offset,
                input.limit,
            ));
        }

        let completed = self.completed_for_owner(&command_id, &context.caller_id)?;
        Ok(line_window(
            command_id,
            &completed.result.stdout,
            input.offset,
            input.limit,
        ))
    }

    pub fn poll(
        &self,
        input: PollCommandInput,
        context: CommandCallContext,
    ) -> Result<CommandPollOutput, CommandServiceError> {
        let command_id = input.command_id;
        if let Some(active) = self.active_for_owner_or_none(&command_id, &context.caller_id)? {
            let stdout = active
                .process
                .read_recent_output(input.last_n_lines.unwrap_or(200));
            return Ok(CommandPollOutput {
                command_id,
                status: CommandStatus::Running,
                exit_code: None,
                output: CommandOutputSnapshot { stdout },
                finalized: None,
            });
        }

        let completed = self.completed_for_owner(&command_id, &context.caller_id)?;
        let stdout = input.last_n_lines.map_or_else(
            || completed.result.stdout.clone(),
            |last_n_lines| ::command::tail_lines(&completed.result.stdout, last_n_lines),
        );
        Ok(CommandPollOutput {
            command_id,
            status: completed.result.status,
            exit_code: completed.result.exit_code,
            output: CommandOutputSnapshot { stdout },
            finalized: Some(Default::default()),
        })
    }

    pub fn cancel(
        &self,
        input: CancelCommandInput,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError> {
        let command_id = input.command_id;
        self.ensure_active_owner(&command_id, &context.caller_id)?;
        let _workspace_id = self.active_workspace_for_command(&command_id)?;
        let output = self
            .process_store
            .update_active(&command_id, |active| {
                active.process.cancel_process();
                active.lifecycle_state = CommandLifecycleState::Cancelled;
                active.cancellation = CancellationState::Requested {
                    requested_at: Instant::now(),
                };
                active.process.read_output_since(0)
            })
            .ok_or_else(|| CommandServiceError::CommandNotFound {
                command_id: command_id.clone(),
            })?;

        Ok(CommandYield {
            command_id: Some(command_id),
            status: CommandStatus::Running,
            exit_code: None,
            output: CommandOutputSnapshot { stdout: output },
            finalized: None,
        })
    }

    pub(crate) fn active_workspace_for_command(
        &self,
        command_id: &crate::command::CommandId,
    ) -> Result<crate::workspace_crate::WorkspaceId, CommandServiceError> {
        self.registry.workspace_for(command_id).ok_or_else(|| {
            CommandServiceError::CommandNotFound {
                command_id: command_id.clone(),
            }
        })
    }

    fn active_for_owner<'a>(
        &'a self,
        command_id: &crate::command::CommandId,
        caller_id: &CallerId,
    ) -> Result<crate::command::ActiveCommandRef<'a>, CommandServiceError> {
        match self.active_for_owner_or_none(command_id, caller_id)? {
            Some(active) => Ok(active),
            None => match self.process_store.completed(command_id) {
                Some(completed) if completed.caller_id == *caller_id => {
                    Err(CommandServiceError::CommandAlreadyCompleted {
                        command_id: command_id.clone(),
                    })
                }
                Some(completed) => Err(CommandServiceError::CommandCallerMismatch {
                    command_id: command_id.clone(),
                    expected: completed.caller_id,
                    actual: caller_id.clone(),
                }),
                None => Err(CommandServiceError::CommandNotFound {
                    command_id: command_id.clone(),
                }),
            },
        }
    }

    fn active_for_owner_or_none<'a>(
        &'a self,
        command_id: &crate::command::CommandId,
        caller_id: &CallerId,
    ) -> Result<Option<crate::command::ActiveCommandRef<'a>>, CommandServiceError> {
        let Some(active) = self.process_store.active(command_id) else {
            return Ok(None);
        };
        if active.caller_id == *caller_id {
            Ok(Some(active))
        } else {
            Err(CommandServiceError::CommandCallerMismatch {
                command_id: command_id.clone(),
                expected: active.caller_id.clone(),
                actual: caller_id.clone(),
            })
        }
    }

    fn completed_for_owner(
        &self,
        command_id: &crate::command::CommandId,
        caller_id: &CallerId,
    ) -> Result<CompletedCommandRecord, CommandServiceError> {
        let completed = self.process_store.completed(command_id).ok_or_else(|| {
            CommandServiceError::CommandNotFound {
                command_id: command_id.clone(),
            }
        })?;
        if completed.caller_id == *caller_id {
            Ok(completed)
        } else {
            Err(CommandServiceError::CommandCallerMismatch {
                command_id: command_id.clone(),
                expected: completed.caller_id,
                actual: caller_id.clone(),
            })
        }
    }

    fn ensure_active_owner(
        &self,
        command_id: &crate::command::CommandId,
        caller_id: &CallerId,
    ) -> Result<(), CommandServiceError> {
        drop(self.active_for_owner(command_id, caller_id)?);
        Ok(())
    }
}

fn line_window(
    command_id: crate::command::CommandId,
    text: &str,
    offset: u64,
    limit: usize,
) -> CommandLinesOutput {
    let lines = text.lines().collect::<Vec<_>>();
    let total_lines = u64::try_from(lines.len()).unwrap_or(u64::MAX);
    let start = usize::try_from(offset)
        .unwrap_or(usize::MAX)
        .min(lines.len());
    let end = start.saturating_add(limit).min(lines.len());
    let output = lines[start..end]
        .iter()
        .enumerate()
        .map(|(index, line)| CommandOutputLine {
            offset: offset.saturating_add(u64::try_from(index).unwrap_or(u64::MAX)),
            text: (*line).to_owned(),
        })
        .collect::<Vec<_>>();
    let next_offset = u64::try_from(end).unwrap_or(u64::MAX);

    CommandLinesOutput {
        command_id,
        offset,
        next_offset,
        total_lines,
        output_truncated: next_offset < total_lines,
        output,
    }
}
