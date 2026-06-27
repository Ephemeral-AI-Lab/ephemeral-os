use std::sync::Arc;

use sandbox_protocol::{error_kind, CliOperationScope, Request, Response};

use super::{forward::forward_sandbox_request, SandboxManagerRouter};

impl SandboxManagerRouter {
    pub async fn dispatch_request(&self, request: Request) -> Response {
        let manager_owned = crate::cli_operation_specs()
            .iter()
            .any(|spec| spec.name == request.op);
        match (&request.scope, manager_owned) {
            (CliOperationScope::System, true) => self.dispatch_manager_request(request).await,
            (CliOperationScope::System, false) => Response::unknown_op(),
            (CliOperationScope::Sandbox { .. }, true) => Response::fault(
                error_kind::INVALID_REQUEST,
                "manager operation requires system scope",
            ),
            (CliOperationScope::Sandbox { .. }, false) => {
                self.forward_sandbox_request(request).await
            }
        }
    }

    async fn dispatch_manager_request(&self, request: Request) -> Response {
        let services = Arc::clone(&self.services);
        match tokio::task::spawn_blocking(move || crate::dispatch_operation(&services, &request))
            .await
        {
            Ok(response) => response,
            Err(error) => Response::fault(
                error_kind::INTERNAL_ERROR,
                format!("manager operation task failed: {error}"),
            ),
        }
    }

    async fn forward_sandbox_request(&self, request: Request) -> Response {
        let services = Arc::clone(&self.services);
        match tokio::task::spawn_blocking(move || forward_sandbox_request(&services, request)).await
        {
            Ok(Ok(response)) => response,
            Ok(Err(error)) => error.into_response(),
            Err(error) => Response::fault(
                error_kind::INTERNAL_ERROR,
                format!("manager forwarding task failed: {error}"),
            ),
        }
    }
}
