use crate::operation::ManagerServices;
use crate::{ManagerError, SandboxRecord};

pub(crate) fn list_sandboxes(
    services: &ManagerServices,
) -> Result<Vec<SandboxRecord>, ManagerError> {
    services.store.list()
}
