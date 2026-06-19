use thiserror::Error;

#[derive(Debug, Error)]
pub enum OperationServiceError {
    #[error(transparent)]
    WorkspaceSession(#[from] crate::workspace_session::WorkspaceSessionError),

    #[error(transparent)]
    Command(#[from] crate::command::CommandServiceError),

    #[error(transparent)]
    WorkspaceRemount(#[from] crate::workspace_remount::WorkspaceRemountError),
}
