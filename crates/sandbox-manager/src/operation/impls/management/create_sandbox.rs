use crate::{CreateSandboxRequest, ManagerError, SandboxState};

use super::{image, record_value, workspace_root};

pub(crate) fn dispatch(
    services: &crate::operation::ManagerServices,
    request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    let image = match image(request) {
        Ok(image) => image,
        Err(response) => return response,
    };
    let workspace_root = match workspace_root(request) {
        Ok(workspace_root) => workspace_root,
        Err(response) => return response,
    };
    let create_request = CreateSandboxRequest {
        image,
        workspace_root: workspace_root.clone(),
    };
    match services.runtime.create_sandbox(&create_request) {
        Ok(created) => {
            let id = created.id;
            if let Err(error) = services.store.create(id.clone(), workspace_root.clone()) {
                return error.into_response();
            }
            let record = match services.store.transition_state(
                &id,
                SandboxState::Creating,
                SandboxState::Ready,
            ) {
                Ok(record) => record,
                Err(error) => return error.into_response(),
            };
            if let Err(error) = services.daemon_installer.install_daemon(&record) {
                return error.into_response();
            }
            let endpoint = match services.daemon_installer.start_daemon(&record) {
                Ok(endpoint) => endpoint,
                Err(error) => return error.into_response(),
            };
            if let Err(error) = services.daemon_installer.check_daemon(&endpoint) {
                return error.into_response();
            }
            match services.store.update_endpoint(&id, Some(endpoint)) {
                Ok(record) => sandbox_protocol::Response::ok(record_value(record)),
                Err(error) => error.into_response(),
            }
        }
        Err(error) => ManagerError::RuntimeFailed {
            message: error.to_string(),
        }
        .into_response(),
    }
}
