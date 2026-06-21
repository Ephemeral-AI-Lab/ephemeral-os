use std::path::PathBuf;

use crate::{ManagerError, SandboxId, SandboxRecord};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CreateSandboxRequest {
    pub id: SandboxId,
    pub workspace_root: PathBuf,
}

pub trait SandboxRuntime: Send + Sync {
    fn create_sandbox(&self, request: &CreateSandboxRequest) -> Result<(), ManagerError>;

    fn destroy_sandbox(&self, record: &SandboxRecord) -> Result<(), ManagerError>;
}
