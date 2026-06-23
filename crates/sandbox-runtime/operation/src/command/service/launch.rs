use sandbox_runtime_command::process::{CommandProcess, CommandProcessSpawn, CommandProcessSpec};
use sandbox_runtime_workspace::WorkspaceEntry;

use std::sync::Arc;

use crate::command::{CommandServiceError, CommandSessionId};

use super::completion::{
    wait_for_completion_yield, CommandCompletionPromise, CommandCompletionWaitOutcome,
};

pub trait CommandLaunchDriver: Send + Sync {
    fn spawn(
        &self,
        spec: CommandProcessSpec,
        workspace_entry: WorkspaceEntry,
        config: &sandbox_runtime_command::CommandConfig,
    ) -> Result<CommandProcess, CommandServiceError>;

    fn start_completion_watcher(
        &self,
        completion: CommandCompletionPromise,
        process: Arc<CommandProcess>,
    ) {
        completion.start_watcher(process);
    }

    fn wait_for_command_yield(
        &self,
        process: &CommandProcess,
        completion: &CommandCompletionPromise,
        yield_time_ms: u64,
        start_offset: u64,
    ) -> CommandCompletionWaitOutcome {
        wait_for_completion_yield(process, completion, yield_time_ms, start_offset)
    }
}

#[derive(Debug, Default)]
pub struct RealCommandLaunchDriver;

impl CommandLaunchDriver for RealCommandLaunchDriver {
    fn spawn(
        &self,
        spec: CommandProcessSpec,
        workspace_entry: WorkspaceEntry,
        config: &sandbox_runtime_command::CommandConfig,
    ) -> Result<CommandProcess, CommandServiceError> {
        let command_session_id = CommandSessionId(spec.id.clone());
        let parts =
            CommandProcessSpawn::prepare(&spec.id, workspace_entry, config).map_err(|error| {
                CommandServiceError::CommandIo {
                    command_session_id: command_session_id.clone(),
                    error: error.to_string(),
                }
            })?;
        let cleanup_parts = parts.clone();
        CommandProcess::spawn(spec, parts).map_err(|error| {
            let command_error = CommandServiceError::CommandIo {
                command_session_id: command_session_id.clone(),
                error: error.to_string(),
            };
            match cleanup_parts.cleanup_artifacts_after_start_failure() {
                Ok(()) => command_error,
                Err(cleanup_error) => CommandServiceError::CommandArtifactCleanupFailed {
                    command_session_id,
                    command_error: Box::new(command_error),
                    artifact_dir: cleanup_parts.artifact_dir(),
                    cleanup_error: cleanup_error.to_string(),
                },
            }
        })
    }
}
