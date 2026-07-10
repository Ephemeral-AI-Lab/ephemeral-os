use std::sync::Arc;

use sandbox_operation_contract::{error, OperationRequest, OperationResponse, OperationScope};

use crate::ProgressSink;

use super::{forward::forward_sandbox_request, SandboxManagerRouter};

impl SandboxManagerRouter {
    pub async fn dispatch_request(&self, request: OperationRequest) -> OperationResponse {
        let manager_owned = manager_owns_operation(&request.op);
        match (&request.scope, manager_owned) {
            (OperationScope::System, true) => self.dispatch_manager_request(request).await,
            (OperationScope::System, false) => OperationResponse::unknown_op(),
            (OperationScope::Sandbox { .. }, true) => OperationResponse::fault(
                error::INVALID_REQUEST,
                "manager operation requires system scope",
            ),
            (OperationScope::Sandbox { .. }, false) => self.forward_sandbox_request(request).await,
        }
    }

    async fn dispatch_manager_request(&self, request: OperationRequest) -> OperationResponse {
        let services = Arc::clone(&self.services);
        match tokio::task::spawn_blocking(move || crate::dispatch_operation(&services, &request))
            .await
        {
            Ok(response) => response,
            Err(error) => OperationResponse::fault(
                error::INTERNAL_ERROR,
                format!("manager operation task failed: {error}"),
            ),
        }
    }

    pub async fn dispatch_request_with_progress(
        &self,
        request: OperationRequest,
        progress: ProgressSink,
    ) -> OperationResponse {
        let manager_owned = manager_owns_operation(&request.op);
        match (&request.scope, manager_owned) {
            (OperationScope::System, true) => {
                let services = Arc::clone(&self.services);
                match tokio::task::spawn_blocking(move || {
                    crate::dispatch_operation_with_progress(&services, &request, progress)
                })
                .await
                {
                    Ok(response) => response,
                    Err(error) => OperationResponse::fault(
                        error::INTERNAL_ERROR,
                        format!("manager operation task failed: {error}"),
                    ),
                }
            }
            _ => self.dispatch_request(request).await,
        }
    }

    async fn forward_sandbox_request(&self, request: OperationRequest) -> OperationResponse {
        let services = Arc::clone(&self.services);
        match tokio::task::spawn_blocking(move || forward_sandbox_request(&services, request)).await
        {
            Ok(Ok(response)) => response,
            Ok(Err(error)) => error.into_response(),
            Err(error) => OperationResponse::fault(
                error::INTERNAL_ERROR,
                format!("manager forwarding task failed: {error}"),
            ),
        }
    }
}

fn manager_owns_operation(op: &str) -> bool {
    crate::operations::registry::operation_entries()
        .iter()
        .any(|entry| entry.spec.name == op)
}
