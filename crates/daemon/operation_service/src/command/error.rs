use std::path::PathBuf;

use thiserror::Error;

use crate::command::CommandId;

#[derive(Debug, Error)]
pub enum CommandServiceError {
    #[error(transparent)]
    WorkspaceManager(#[from] crate::workspace_manager::WorkspaceManagerError),

    #[error("command service behavior is not implemented yet")]
    NotImplemented,

    #[error("workspace root mismatch: expected {expected:?}, actual {actual:?}")]
    WorkspaceRootMismatch { expected: PathBuf, actual: PathBuf },

    #[error("duplicate command id: {command_id:?}")]
    DuplicateCommandId { command_id: CommandId },

    #[error("active command limit reached: active {active}, max {max}")]
    CommandAdmissionLimit { active: usize, max: usize },

    #[error("command reservation belongs to a different process store")]
    ReservationStoreMismatch,
}
