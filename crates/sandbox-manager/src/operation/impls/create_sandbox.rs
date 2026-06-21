use crate::{CreateSandboxRequest, ManagerError, SandboxState};

use super::{record_value, sandbox_id, workspace_root};

pub(crate) fn dispatch(
    services: &crate::operation::ManagerServices,
    request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    let id = match sandbox_id(request) {
        Ok(id) => id,
        Err(response) => return response,
    };
    let workspace_root = match workspace_root(request) {
        Ok(workspace_root) => workspace_root,
        Err(response) => return response,
    };
    if let Err(error) = services.store.create(id.clone(), workspace_root.clone()) {
        return error.into_response();
    }
    let create_request = CreateSandboxRequest {
        id: id.clone(),
        workspace_root,
    };
    match services.runtime.create_sandbox(&create_request) {
        Ok(()) => {
            match services
                .store
                .transition_state(&id, SandboxState::Creating, SandboxState::Ready)
            {
                Ok(record) => sandbox_protocol::Response::ok(record_value(record)),
                Err(error) => error.into_response(),
            }
        }
        Err(error) => {
            let _ = services.store.set_state(&id, SandboxState::Failed);
            ManagerError::RuntimeFailed {
                message: error.to_string(),
            }
            .into_response()
        }
    }
}
