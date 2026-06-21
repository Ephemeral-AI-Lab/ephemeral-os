use crate::{CreateSandboxRequest, ManagerError, SandboxState};

use super::{image, workspace_root};

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
            match services
                .store
                .transition_state(&id, SandboxState::Creating, SandboxState::Ready)
            {
                Ok(record) => sandbox_protocol::Response::ok(serde_json::json!({
                    "sandbox_id": record.id.as_str(),
                    "workspace_root": record.workspace_root.to_string_lossy(),
                })),
                Err(error) => error.into_response(),
            }
        }
        Err(error) => ManagerError::RuntimeFailed {
            message: error.to_string(),
        }
        .into_response(),
    }
}
