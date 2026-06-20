use std::sync::Arc;

use sandbox_protocol::{OperationScope, Request};

use super::{forward::forward_sandbox_request, SandboxManagerServer};

impl SandboxManagerServer {
    pub(super) async fn dispatch_request(&self, request: Request) -> serde_json::Value {
        let manager_owned = crate::operation_specs()
            .iter()
            .any(|spec| spec.name == request.op);
        match (&request.scope, manager_owned) {
            (OperationScope::System, true) => self.dispatch_manager_request(request).await,
            (OperationScope::System, false) => {
                sandbox_protocol::Response::unknown_op().into_json_value()
            }
            (OperationScope::Sandbox { .. }, true) => super::error::error_response(
                sandbox_protocol::error_kind::INVALID_REQUEST,
                "manager operation requires system scope",
                serde_json::json!({}),
            ),
            (OperationScope::Sandbox { .. }, false) => self.forward_sandbox_request(request).await,
        }
    }

    async fn dispatch_manager_request(&self, request: Request) -> serde_json::Value {
        let services = Arc::clone(&self.services);
        match tokio::task::spawn_blocking(move || {
            crate::dispatch_operation(&services, &request).into_json_value()
        })
        .await
        {
            Ok(response) => response,
            Err(error) => super::error::error_response(
                sandbox_protocol::error_kind::INTERNAL_ERROR,
                format!("manager operation task failed: {error}"),
                serde_json::json!({}),
            ),
        }
    }

    async fn forward_sandbox_request(&self, request: Request) -> serde_json::Value {
        let services = Arc::clone(&self.services);
        match tokio::task::spawn_blocking(move || {
            forward_sandbox_request(&services, request)
                .map(sandbox_protocol::Response::into_json_value)
        })
        .await
        {
            Ok(Ok(response)) => response,
            Ok(Err(error)) => error.into_response().into_json_value(),
            Err(error) => super::error::error_response(
                sandbox_protocol::error_kind::INTERNAL_ERROR,
                format!("manager forwarding task failed: {error}"),
                serde_json::json!({}),
            ),
        }
    }
}
