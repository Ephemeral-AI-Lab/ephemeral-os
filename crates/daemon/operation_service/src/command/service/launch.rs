use command::process::{
    CommandProcess, CommandProcessExit, CommandProcessSpawn, CommandProcessSpec,
};
use command::yield_wait_loop::{wait_for_yield, WaitOutcome};

use crate::command::{CommandId, CommandServiceError};

pub trait CommandLaunchDriver: Send + Sync {
    fn spawn(
        &self,
        spec: CommandProcessSpec,
        parts: CommandProcessSpawn<'_>,
    ) -> Result<CommandProcess, CommandServiceError>;

    fn wait_for_initial_yield(
        &self,
        process: &CommandProcess,
        config: &command::CommandConfig,
        yield_time_ms: u64,
        start_offset: u64,
    ) -> WaitOutcome<CommandProcessExit>;
}

#[derive(Debug, Default)]
pub struct RealCommandLaunchDriver;

impl CommandLaunchDriver for RealCommandLaunchDriver {
    fn spawn(
        &self,
        spec: CommandProcessSpec,
        parts: CommandProcessSpawn<'_>,
    ) -> Result<CommandProcess, CommandServiceError> {
        let command_id = CommandId(spec.id.clone());
        CommandProcess::spawn(spec, parts).map_err(|error| CommandServiceError::CommandIo {
            command_id,
            error: error.to_string(),
        })
    }

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
