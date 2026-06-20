use command::process::{
    CommandProcess, CommandProcessExit, CommandProcessSpawn, CommandProcessSpec,
};
use command::yield_wait_loop::{wait_for_yield, WaitOutcome};
use workspace::WorkspaceEntry;

use crate::command::{CommandServiceError, CommandSessionId};

pub trait CommandLaunchDriver: Send + Sync {
    fn spawn(
        &self,
        spec: CommandProcessSpec,
        workspace_entry: WorkspaceEntry,
        config: &command::CommandConfig,
    ) -> Result<CommandProcess, CommandServiceError>;

    fn wait_for_initial_yield(
        &self,
        process: &CommandProcess,
        config: &command::CommandConfig,
        yield_time_ms: u64,
        start_offset: u64,
    ) -> WaitOutcome<CommandProcessExit> {
        wait_for_yield(process, config, yield_time_ms, start_offset)
    }
}

#[derive(Debug, Default)]
pub struct RealCommandLaunchDriver;

impl CommandLaunchDriver for RealCommandLaunchDriver {
    fn spawn(
        &self,
        spec: CommandProcessSpec,
        workspace_entry: WorkspaceEntry,
        config: &command::CommandConfig,
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
