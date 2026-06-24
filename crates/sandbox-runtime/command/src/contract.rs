//! Command substrate request DTOs and error type.

use std::path::{Path, PathBuf};

use sandbox_runtime_namespace_execution::NamespaceExecutionTerminalStatus;
use thiserror::Error;

/// The trimmed terminal projection of a finished command: terminal status, exit
/// code, and total wall time. The command op's `finalize` builds it from a
/// `RunnerOutcome`; the engine promise retains it. `Copy` so the non-consuming
/// `resolved()` peek that serves terminal reads is trivial.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CommandTerminalResult {
    pub status: NamespaceExecutionTerminalStatus,
    pub exit_code: i64,
    pub command_total_time_seconds: f64,
}

#[derive(Debug, Error)]
pub enum CommandError {
    /// A workspace-tier failure surfaced through the command lifecycle; the
    /// substrate carries only the rendered message.
    #[error("{0}")]
    Workspace(String),
    #[error("command not found: {0}")]
    NotFound(String),
    #[error("invalid command request: {0}")]
    InvalidRequest(String),
    #[error("command io error: {0}")]
    Io(String),
    #[error("command artifact write failed for {artifact} at {}: {error}", path.display())]
    ArtifactWrite {
        artifact: &'static str,
        path: PathBuf,
        error: String,
    },
}

impl From<std::io::Error> for CommandError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error.to_string())
    }
}

impl CommandError {
    #[must_use]
    pub fn artifact_write(
        artifact: &'static str,
        path: impl AsRef<Path>,
        error: impl std::fmt::Display,
    ) -> Self {
        Self::ArtifactWrite {
            artifact,
            path: path.as_ref().to_path_buf(),
            error: error.to_string(),
        }
    }
}
