use thiserror::Error;

#[derive(Debug, Error)]
pub enum WorkspaceRemountError {
    #[error(transparent)]
    WorkspaceManager(#[from] crate::workspace_manager::WorkspaceManagerError),

    #[error(transparent)]
    Command(#[from] crate::command::CommandServiceError),
}
