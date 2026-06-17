use thiserror::Error;

#[derive(Debug, Error)]
pub enum WorkspaceRemountError {
    #[error("workspace remount behavior is not implemented yet")]
    NotImplemented,
}
