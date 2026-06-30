use std::path::PathBuf;

use crate::{ManagerError, SandboxId, SandboxRecord, SharedBaseMount};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CreateSandboxRequest {
    pub image: String,
    pub workspace_root: PathBuf,
    pub shared_base: Option<SharedBaseMount>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CreateSandboxResult {
    pub id: SandboxId,
}

pub trait SandboxRuntime: Send + Sync {
    fn create_sandbox(
        &self,
        request: &CreateSandboxRequest,
    ) -> Result<CreateSandboxResult, ManagerError>;

    fn destroy_sandbox(&self, record: &SandboxRecord) -> Result<(), ManagerError>;
}
