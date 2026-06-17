use std::time::Instant;

use crate::command::{
    ActiveCommandProcess, CancellationState, CommandCallContext, CommandFinalizePolicy,
    CommandLifecycleState, CommandOutputSnapshot, CommandServiceError, CommandStatus,
    CommandTraceOrigin, CommandTranscriptStore, CommandYield, ExecCommandInput, FinalizationState,
};
use crate::workspace_crate::WorkspaceId;
use crate::workspace_manager::WorkspaceSessionHandler;

use super::service::CommandOperationService;

impl CommandOperationService {
    pub fn exec_command(
        &self,
        input: ExecCommandInput,
        workspace: Option<WorkspaceSessionHandler>,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError> {
        if input.cmd.trim().is_empty() {
            return Err(CommandServiceError::InvalidCommand {
                message: "cmd must be non-empty".to_owned(),
            });
        }
        if input.caller_id != context.caller_id {
            return Err(CommandServiceError::InvalidCommand {
                message: "exec caller must match command call context".to_owned(),
            });
        }

        let command_id = self.process_store().allocate_command_id();
        let reservation = self.process_store().try_reserve()?;
        let is_session_command = workspace.is_some();
        let handler = match workspace {
            Some(handler) => handler,
            None => self.workspace().create_private_host_workspace(
                context.caller_id.clone(),
                input.workspace_root.clone(),
            )?,
        };
        let workspace_id = handler.workspace_id.clone();
        let finalize_policy = finalize_policy(is_session_command, &workspace_id);
        let _yield_time_ms = input
            .yield_time_ms
            .unwrap_or(self.config().default_yield_time_ms);

        self.registry()
            .bind(command_id.clone(), workspace_id.clone())?;
        let record = ActiveCommandProcess {
            command_id: command_id.clone(),
            caller_id: context.caller_id.clone(),
            workspace_id,
            process: ::command::CommandProcess::new(::command::CommandProcessSpec {
                id: command_id.0.clone(),
                caller_id: context.caller_id.0.clone(),
                command: input.cmd,
                timeout_seconds: input.timeout_seconds,
            }),
            transcript: CommandTranscriptStore {
                transcript_path: Some(
                    self.config()
                        .scratch_root
                        .join(&command_id.0)
                        .join("transcript.log"),
                ),
            },
            finalize_policy,
            lifecycle_state: CommandLifecycleState::Running,
            cancellation: CancellationState::None,
            finalization: FinalizationState::NotStarted,
            trace_origin: CommandTraceOrigin,
            started_at: Instant::now(),
        };
        if let Err(error) = self.process_store().insert_active(reservation, record) {
            let _ = self.registry().unbind(&command_id);
            return Err(error);
        }

        Ok(CommandYield {
            command_id: Some(command_id),
            status: CommandStatus::Running,
            exit_code: None,
            output: CommandOutputSnapshot::default(),
            finalized: None,
        })
    }
}

fn finalize_policy(is_session_command: bool, workspace_id: &WorkspaceId) -> CommandFinalizePolicy {
    if is_session_command {
        CommandFinalizePolicy::Session {
            workspace_id: workspace_id.clone(),
        }
    } else {
        CommandFinalizePolicy::OneShotPublishThenDestroy {
            workspace_id: workspace_id.clone(),
        }
    }
}
