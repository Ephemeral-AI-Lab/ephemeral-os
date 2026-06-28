use crate::operation::ManagerServices;
use crate::{ManagerError, SandboxId, SandboxRecord};

pub(crate) fn inspect_sandbox(
    services: &ManagerServices,
    id: &SandboxId,
) -> Result<SandboxRecord, ManagerError> {
    services.store.inspect(id)
}
