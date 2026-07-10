use std::sync::Arc;

use sandbox_operation_catalog::{internal, routes};
use sandbox_operation_contract::{
    error, OperationRequest, OperationResponse, OperationRouteSpec, OperationScopeKind,
    OperationVisibility,
};

use crate::ProgressSink;

use super::{forward::forward_sandbox_request, SandboxManagerRouter};

impl SandboxManagerRouter {
    pub async fn dispatch_request(&self, request: OperationRequest) -> OperationResponse {
        if crate::operations::has_operation_handler(request.scope.kind(), &request.op) {
            return self.dispatch_manager_request(request).await;
        }
        if request.scope.kind() == OperationScopeKind::System {
            return OperationResponse::unknown_op();
        }
        if matches_route(&request, &internal::migration::ROUTE) {
            return self.forward_sandbox_request(request).await;
        }
        if is_canonical_internal_route(&request) {
            return OperationResponse::fault(
                error::INVALID_REQUEST,
                "internal operation is not publicly dispatchable",
            );
        }
        if request.op == internal::runtime::FILE_LIST {
            return OperationResponse::fault(
                error::INVALID_REQUEST,
                "file_list is available only through daemon HTTP",
            );
        }
        if is_wrong_scope_manager_operation(&request) {
            return OperationResponse::fault(
                error::INVALID_REQUEST,
                "manager operation requires system scope",
            );
        }
        self.forward_sandbox_request(request).await
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
        if crate::operations::has_operation_handler(request.scope.kind(), &request.op) {
            let services = Arc::clone(&self.services);
            return match tokio::task::spawn_blocking(move || {
                crate::dispatch_operation_with_progress(&services, &request, progress)
            })
            .await
            {
                Ok(response) => response,
                Err(error) => OperationResponse::fault(
                    error::INTERNAL_ERROR,
                    format!("manager operation task failed: {error}"),
                ),
            };
        }
        self.dispatch_request(request).await
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

fn matches_route(request: &OperationRequest, route: &OperationRouteSpec) -> bool {
    request.scope.kind() == route.scope_kind && request.op == route.operation
}

fn is_canonical_internal_route(request: &OperationRequest) -> bool {
    internal::runtime::ROUTES.iter().any(|route| {
        route.visibility == OperationVisibility::Internal && matches_route(request, route)
    })
}

fn is_wrong_scope_manager_operation(request: &OperationRequest) -> bool {
    routes::manager_routes()
        .iter()
        .any(|route| route.operation == request.op && route.scope_kind != request.scope.kind())
}
