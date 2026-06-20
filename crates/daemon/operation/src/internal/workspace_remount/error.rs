use thiserror::Error;

#[derive(Debug, Error)]
pub enum WorkspaceRemountError {
    #[error(transparent)]
    WorkspaceSession(#[from] crate::workspace_session::WorkspaceSessionError),

    #[error(transparent)]
    Command(#[from] crate::command::CommandServiceError),
}
