use crate::{ManagerError, SandboxState};

use super::{record_value, sandbox_id};

pub(crate) fn dispatch(
    services: &crate::operation::ManagerServices,
    request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    let id = match sandbox_id(request) {
        Ok(id) => id,
        Err(response) => return response,
    };
    let current = match services.store.inspect(&id) {
        Ok(record) => record,
        Err(error) => return error.into_response(),
    };
    if matches!(
        current.state,
        SandboxState::Creating | SandboxState::Stopping
    ) {
        return ManagerError::InvalidStateTransition {
            id,
            from: current.state,
            to: SandboxState::Stopping,
        }
        .into_response();
    }
    let stopping =
        match services
            .store
            .transition_state(&current.id, current.state, SandboxState::Stopping)
        {
            Ok(record) => record,
            Err(error) => return error.into_response(),
        };
    match services.runtime.destroy_sandbox(&stopping) {
        Ok(()) => {
            if let Err(error) = services
                .store
                .set_state(&stopping.id, SandboxState::Stopped)
            {
                return error.into_response();
            }
            match services.store.remove(&stopping.id) {
                Ok(record) => sandbox_protocol::Response::ok(record_value(record)),
                Err(error) => error.into_response(),
            }
        }
        Err(error) => {
            let _ = services.store.set_state(&stopping.id, SandboxState::Failed);
            ManagerError::RuntimeFailed {
                message: error.to_string(),
            }
            .into_response()
        }
    }
}
