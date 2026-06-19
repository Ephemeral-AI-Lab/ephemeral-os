use thiserror::Error;

use crate::workspace_crate::{CallerId, WorkspaceError, WorkspaceId};

#[derive(Debug, Error)]
pub enum WorkspaceSessionError {
    #[error(transparent)]
    Workspace(#[from] WorkspaceError),

    #[error("workspace session manager lock poisoned")]
    LockPoisoned,

    #[error("workspace session already exists: {workspace_session_id:?}")]
    DuplicateWorkspaceSessionId { workspace_session_id: WorkspaceId },

    #[error("workspace session not found: {workspace_session_id:?}")]
    NotFound { workspace_session_id: WorkspaceId },

    #[error("workspace session is closing: {workspace_session_id:?}")]
    Closing { workspace_session_id: WorkspaceId },

    #[error("workspace remount already pending: {workspace_session_id:?}")]
    RemountAlreadyPending { workspace_session_id: WorkspaceId },

    #[error("workspace remount is not pending: {workspace_session_id:?}")]
    RemountNotPending { workspace_session_id: WorkspaceId },

    #[error("workspace remount returned mismatched workspace session id: expected {expected:?}, actual {actual:?}")]
    RemountWorkspaceSessionIdMismatch {
        expected: WorkspaceId,
        actual: WorkspaceId,
    },

    #[error(
        "workspace session caller mismatch for {workspace_session_id:?}: expected {expected:?}, actual {actual:?}"
    )]
    CallerMismatch {
        workspace_session_id: WorkspaceId,
        expected: CallerId,
        actual: CallerId,
    },

    #[error(
        "workspace cleanup after create failure failed for {workspace_session_id:?}: {rollback_error}"
    )]
    CreateRollbackFailed {
        workspace_session_id: WorkspaceId,
        insert_error: Box<WorkspaceSessionError>,
        rollback_error: WorkspaceError,
    },
}
