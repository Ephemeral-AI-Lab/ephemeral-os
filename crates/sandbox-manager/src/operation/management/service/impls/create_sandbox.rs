use std::path::PathBuf;

use crate::operation::ManagerServices;
use crate::{
    CreateSandboxRequest, ManagerError, SandboxDaemonEndpoint, SandboxRecord, SandboxState,
};

pub(crate) struct CreateSandboxInput {
    pub(crate) image: String,
    pub(crate) workspace_root: PathBuf,
}

pub(crate) fn create_sandbox(
    services: &ManagerServices,
    input: CreateSandboxInput,
) -> Result<SandboxRecord, ManagerError> {
    let CreateSandboxInput {
        image,
        workspace_root,
    } = input;
    let create_request = CreateSandboxRequest {
        image,
        workspace_root: workspace_root.clone(),
    };
    let created = services.runtime.create_sandbox(&create_request)?;
    let id = created.id;
    let record = match services.store.create(id.clone(), workspace_root.clone()) {
        Ok(record) => record,
        Err(error) => {
            let untracked = SandboxRecord::new(id, workspace_root, SandboxState::Creating);
            let _ = services.runtime.destroy_sandbox(&untracked);
            return Err(error);
        }
    };
    let endpoint = match provision_daemon(services, &record) {
        Ok(endpoint) => endpoint,
        Err(error) => {
            rollback(services, &record);
            return Err(error);
        }
    };
    if let Err(error) = services.store.update_endpoint(&id, Some(endpoint)) {
        rollback(services, &record);
        return Err(error);
    }
    match services
        .store
        .transition_state(&id, SandboxState::Creating, SandboxState::Ready)
    {
        Ok(ready) => Ok(ready),
        Err(error) => {
            rollback(services, &record);
            Err(error)
        }
    }
}

fn provision_daemon(
    services: &ManagerServices,
    record: &SandboxRecord,
) -> Result<SandboxDaemonEndpoint, ManagerError> {
    services.daemon_installer.install_daemon(record)?;
    let endpoint = services.daemon_installer.start_daemon(record)?;
    services.daemon_installer.check_daemon(record, &endpoint)?;
    Ok(endpoint)
}

fn rollback(services: &ManagerServices, record: &SandboxRecord) {
    let _ = services.daemon_installer.stop_daemon(record);
    let _ = services.runtime.destroy_sandbox(record);
    let _ = services.store.remove(&record.id);
}
