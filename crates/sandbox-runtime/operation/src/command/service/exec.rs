use std::path::{Path, PathBuf};
use std::time::Instant;

use sandbox_runtime_namespace_execution::{NamespaceExecutionError, RunnerOutcome, ShellOperation};

use crate::command::CommandTerminalResult;

pub(crate) struct ExecCommand {
    pub(crate) command: String,
    pub(crate) timeout_seconds: Option<f64>,
    pub(crate) transcript_path: PathBuf,
    pub(crate) started_at: Instant,
}

impl ShellOperation for ExecCommand {
    type Output = CommandTerminalResult;

    fn operation_name(&self) -> &'static str {
        "exec_command"
    }

    fn command(&self) -> &str {
        &self.command
    }

    fn timeout_seconds(&self) -> Option<f64> {
        self.timeout_seconds
    }

    fn transcript_path(&self) -> Option<&Path> {
        Some(&self.transcript_path)
    }

    fn finalize(
        self: Box<Self>,
        outcome: RunnerOutcome,
    ) -> Result<CommandTerminalResult, NamespaceExecutionError> {
        Ok(CommandTerminalResult {
            status: outcome.status(),
            exit_code: outcome.exit_code(),
            command_total_time_seconds: self.started_at.elapsed().as_secs_f64(),
        })
    }
}
