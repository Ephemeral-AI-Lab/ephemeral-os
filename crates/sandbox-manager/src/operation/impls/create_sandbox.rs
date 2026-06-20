use crate::{ManagerError, SandboxState};

use super::{record_value, sandbox_id};

pub(crate) fn dispatch(
    services: &crate::operation::ManagerServices,
    request: sandbox_protocol::OperationRequest<'_>,
) -> sandbox_protocol::OperationResponse {
    let id = match sandbox_id(&request) {
        Ok(id) => id,
        Err(response) => return response,
    };
    if let Err(error) = services.store.create(id.clone()) {
        return error.into_response();
    }
    match services.runtime.create_sandbox(&id) {
        Ok(()) => {
            match services
                .store
                .transition_state(&id, SandboxState::Creating, SandboxState::Ready)
            {
                Ok(record) => {
                    sandbox_protocol::OperationResponse::ok(&request, record_value(record))
                }
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
