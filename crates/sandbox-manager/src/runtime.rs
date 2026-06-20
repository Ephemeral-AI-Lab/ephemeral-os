use crate::{ManagerResult, SandboxId, SandboxRecord};

pub trait SandboxRuntime: Send + Sync {
    fn create_sandbox(&self, id: &SandboxId) -> ManagerResult<()>;

    fn destroy_sandbox(&self, record: &SandboxRecord) -> ManagerResult<()>;
}
