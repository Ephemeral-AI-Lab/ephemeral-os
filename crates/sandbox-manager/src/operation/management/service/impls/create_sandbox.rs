use std::path::PathBuf;

use crate::operation::ManagerServices;
use crate::{
    CreateSandboxRequest, ManagerError, ProgressSink, SandboxDaemonEndpoint, SandboxRecord,
    SandboxState,
};

pub(crate) struct CreateSandboxInput {
    pub(crate) image: String,
    pub(crate) workspace_root: PathBuf,
}

pub(crate) fn create_sandbox(
    services: &ManagerServices,
    input: CreateSandboxInput,
    progress: &ProgressSink,
) -> Result<SandboxRecord, ManagerError> {
    let CreateSandboxInput {
        image,
        workspace_root,
    } = input;
    progress.emit(format!(
        "creating runtime sandbox for {}",
        workspace_root.display()
    ));
    let create_request = CreateSandboxRequest {
        image,
        workspace_root: workspace_root.clone(),
    };
    let created = match services.runtime.create_sandbox(&create_request) {
        Ok(created) => {
            progress.emit("runtime sandbox created");
            created
        }
        Err(error) => {
            progress.emit(error.to_string());
            return Err(error);
        }
    };
    let id = created.id;
    progress.emit("recording sandbox");
    let record = match services.store.create(id.clone(), workspace_root.clone()) {
        Ok(record) => record,
        Err(error) => {
            progress.emit(error.to_string());
            let untracked = SandboxRecord::new(id, workspace_root, SandboxState::Creating);
            let _ = services.runtime.destroy_sandbox(&untracked);
            return Err(error);
        }
    };
    progress.emit("sandbox recorded");
    let endpoint = match provision_daemon(services, &record, progress) {
        Ok(endpoint) => endpoint,
        Err(error) => {
            progress.emit("destroying failed sandbox");
            rollback(services, &record);
            progress.emit("failed sandbox destroyed");
            return Err(error);
        }
    };
    if let Err(error) = services.store.update_endpoint(&id, Some(endpoint)) {
        progress.emit(error.to_string());
        rollback(services, &record);
        return Err(error);
    }
    progress.emit("marking sandbox ready");
    match services
        .store
        .transition_state(&id, SandboxState::Creating, SandboxState::Ready)
    {
        Ok(ready) => {
            progress.emit("sandbox is ready");
            Ok(ready)
        }
        Err(error) => {
            progress.emit(error.to_string());
            rollback(services, &record);
            Err(error)
        }
    }
}

fn provision_daemon(
    services: &ManagerServices,
    record: &SandboxRecord,
    progress: &ProgressSink,
) -> Result<SandboxDaemonEndpoint, ManagerError> {
    progress.emit("installing daemon assets");
    if let Err(error) = services.daemon_installer.install_daemon(record) {
        progress.emit(error.to_string());
        return Err(error);
    }
    progress.emit("daemon assets installed");

    progress.emit("starting daemon");
    let endpoint = match services.daemon_installer.start_daemon(record) {
        Ok(endpoint) => {
            progress.emit(format!(
                "daemon published on {}:{}",
                endpoint.host, endpoint.port
            ));
            endpoint
        }
        Err(error) => {
            progress.emit(error.to_string());
            return Err(error);
        }
    };

    progress.emit("waiting for daemon readiness");
    if let Err(error) = services
        .daemon_installer
        .check_daemon_with_progress(record, &endpoint, progress)
    {
        progress.emit(error.to_string());
        return Err(error);
    }
    progress.emit("daemon is ready");
    Ok(endpoint)
}

fn rollback(services: &ManagerServices, record: &SandboxRecord) {
    let _ = services.daemon_installer.stop_daemon(record);
    let _ = services.runtime.destroy_sandbox(record);
    let _ = services.store.remove(&record.id);
}
