use super::ManagerServices;
use crate::ProgressSink;
use sandbox_operation_contract::{OperationRequest, OperationResponse, OperationSpec};

#[derive(Clone, Copy)]
pub(crate) struct ManagerOperationEntry {
    pub(crate) spec: &'static OperationSpec,
    pub(crate) dispatch: fn(&ManagerServices, &OperationRequest) -> OperationResponse,
}

impl ManagerOperationEntry {
    #[must_use]
    pub(crate) const fn new(
        spec: &'static OperationSpec,
        dispatch: fn(&ManagerServices, &OperationRequest) -> OperationResponse,
    ) -> Self {
        Self { spec, dispatch }
    }
}

#[must_use]
pub fn dispatch_operation(
    services: &ManagerServices,
    request: &OperationRequest,
) -> OperationResponse {
    super::registry::operation_entries()
        .iter()
        .find(|entry| entry.spec.name == request.op)
        .map_or_else(OperationResponse::unknown_op, |entry| {
            (entry.dispatch)(services, request)
        })
}

#[must_use]
pub fn dispatch_operation_with_progress(
    services: &ManagerServices,
    request: &OperationRequest,
    progress: ProgressSink,
) -> OperationResponse {
    if request.op == "create_sandbox" {
        return super::registry::management_operations::dispatch_create_sandbox_with_progress(
            services, request, &progress,
        );
    }
    dispatch_operation(services, request)
}
