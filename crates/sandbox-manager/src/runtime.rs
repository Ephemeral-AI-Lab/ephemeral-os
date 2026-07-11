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

/// Cumulative, read-only resource counters reported by the container runtime.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SandboxResourceMetrics {
    pub cpu_usage_usec: u64,
    pub memory_current_bytes: Option<u64>,
    pub memory_limit_bytes: Option<u64>,
    pub io_read_bytes: u64,
    pub io_write_bytes: u64,
}

pub trait SandboxRuntime: Send + Sync {
    fn list_images(&self) -> Result<Vec<String>, ManagerError> {
        Err(ManagerError::RuntimeFailed {
            message: "sandbox runtime does not support Docker image discovery".to_owned(),
        })
    }

    fn create_sandbox(
        &self,
        request: &CreateSandboxRequest,
    ) -> Result<CreateSandboxResult, ManagerError>;

    fn destroy_sandbox(&self, record: &SandboxRecord) -> Result<(), ManagerError>;

    fn read_sandbox_resource_metrics(
        &self,
        _id: &SandboxId,
    ) -> Result<SandboxResourceMetrics, ManagerError> {
        Err(ManagerError::RuntimeFailed {
            message: "sandbox runtime does not support read-only resource metrics".to_owned(),
        })
    }
}
