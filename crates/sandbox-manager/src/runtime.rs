use crate::{ManagerError, SandboxId, SandboxRecord};

pub trait SandboxRuntime: Send + Sync {
    fn create_sandbox(&self, id: &SandboxId) -> Result<(), ManagerError>;

    fn destroy_sandbox(&self, record: &SandboxRecord) -> Result<(), ManagerError>;
}
