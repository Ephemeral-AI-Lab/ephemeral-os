use std::path::PathBuf;

use thiserror::Error;

use crate::command::{CommandFinalizedMetadata, CommandId};
use crate::workspace_crate::{CallerId, WorkspaceId};

#[derive(Debug, Error)]
pub enum CommandServiceError {
    #[error(transparent)]
    WorkspaceManager(#[from] crate::workspace_manager::WorkspaceManagerError),

    #[error("workspace root mismatch: expected {expected:?}, actual {actual:?}")]
    WorkspaceRootMismatch { expected: PathBuf, actual: PathBuf },

    #[error("invalid command request: {message}")]
    InvalidCommand { message: String },

    #[error("command not found: {command_id:?}")]
    CommandNotFound { command_id: CommandId },

    #[error(
        "command caller mismatch for {command_id:?}: expected {expected:?}, actual {actual:?}"
    )]
    CommandCallerMismatch {
        command_id: CommandId,
        expected: CallerId,
        actual: CallerId,
    },

    #[error(
        "command workspace mismatch for {command_id:?}: expected {expected:?}, actual {actual:?}"
    )]
    CommandWorkspaceMismatch {
        command_id: CommandId,
        expected: WorkspaceId,
        actual: WorkspaceId,
    },

    #[error("command already completed: {command_id:?}")]
    CommandAlreadyCompleted { command_id: CommandId },

    #[error("command io failed for {command_id:?}: {error}")]
    CommandIo {
        command_id: CommandId,
        error: String,
    },

    #[error("command finalization failed for {command_id:?}: {error}")]
    CommandFinalizationFailed {
        command_id: CommandId,
        error: String,
        finalized: Option<Box<CommandFinalizedMetadata>>,
    },

    #[error("duplicate command id: {command_id:?}")]
    DuplicateCommandId { command_id: CommandId },

    #[error("active command limit reached: active {active}, max {max}")]
    CommandAdmissionLimit { active: usize, max: usize },

    #[error("command reservation belongs to a different process store")]
    ReservationStoreMismatch,

    #[error(
        "one-shot workspace cleanup failed for {command_id:?} after command start failure: command error: {command_error}; cleanup error: {cleanup_error}"
    )]
    OneShotWorkspaceCleanupFailed {
        command_id: CommandId,
        command_error: Box<CommandServiceError>,
        cleanup_error: crate::workspace_manager::WorkspaceManagerError,
    },

    #[error(
        "command artifact cleanup failed for {command_id:?} after command start failure at {artifact_dir:?}: command error: {command_error}; cleanup error: {cleanup_error}"
    )]
    CommandArtifactCleanupFailed {
        command_id: CommandId,
        command_error: Box<CommandServiceError>,
        artifact_dir: PathBuf,
        cleanup_error: String,
    },
}
