use thiserror::Error;

use crate::workspace_crate::{CallerId, WorkspaceError, WorkspaceId};

#[derive(Debug, Error)]
pub enum WorkspaceManagerError {
    #[error(transparent)]
    Workspace(#[from] WorkspaceError),

    #[error("workspace session manager lock poisoned")]
    LockPoisoned,

    #[error("workspace session already exists: {workspace_id:?}")]
    DuplicateWorkspaceId { workspace_id: WorkspaceId },

    #[error("workspace session not found: {workspace_id:?}")]
    NotFound { workspace_id: WorkspaceId },

    #[error("workspace session is closing: {workspace_id:?}")]
    Closing { workspace_id: WorkspaceId },

    #[error("workspace remount already pending: {workspace_id:?}")]
    RemountAlreadyPending { workspace_id: WorkspaceId },

    #[error("workspace remount is not pending: {workspace_id:?}")]
    RemountNotPending { workspace_id: WorkspaceId },

    #[error("workspace remount returned mismatched workspace id: expected {expected:?}, actual {actual:?}")]
    RemountWorkspaceIdMismatch {
        expected: WorkspaceId,
        actual: WorkspaceId,
    },

    #[error(
        "workspace session caller mismatch for {workspace_id:?}: expected {expected:?}, actual {actual:?}"
    )]
    CallerMismatch {
        workspace_id: WorkspaceId,
        expected: CallerId,
        actual: CallerId,
    },

    #[error(
        "workspace cleanup after create failure failed for {workspace_id:?}: {rollback_error}"
    )]
    CreateRollbackFailed {
        workspace_id: WorkspaceId,
        insert_error: Box<WorkspaceManagerError>,
        rollback_error: WorkspaceError,
    },
}
