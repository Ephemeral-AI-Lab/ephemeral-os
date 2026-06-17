use thiserror::Error;

#[derive(Debug, Error)]
pub enum OperationServiceError {
    #[error(transparent)]
    WorkspaceManager(#[from] crate::workspace_manager::WorkspaceManagerError),

    #[error(transparent)]
    Command(#[from] crate::command::CommandServiceError),

    #[error(transparent)]
    WorkspaceRemount(#[from] crate::workspace_remount::WorkspaceRemountError),
}
