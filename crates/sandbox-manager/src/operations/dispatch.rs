use super::ManagerServices;
use crate::ProgressSink;
use sandbox_operation_catalog::manager::CREATE_SANDBOX_SPEC;
use sandbox_operation_contract::{
    OperationRequest, OperationResponse, OperationScopeKind, OperationSpec,
};

#[derive(Clone, Copy)]
pub(crate) struct ManagerOperationEntry {
    pub(crate) scope_kind: OperationScopeKind,
    pub(crate) spec: &'static OperationSpec,
    pub(crate) dispatch: fn(&ManagerServices, &OperationRequest) -> OperationResponse,
}

impl ManagerOperationEntry {
    #[must_use]
    pub(crate) const fn new(
        scope_kind: OperationScopeKind,
        spec: &'static OperationSpec,
        dispatch: fn(&ManagerServices, &OperationRequest) -> OperationResponse,
    ) -> Self {
        Self {
            scope_kind,
            spec,
            dispatch,
        }
    }
}

pub(crate) fn has_operation_handler(scope_kind: OperationScopeKind, operation: &str) -> bool {
    operation_entry(scope_kind, operation).is_some()
}

#[must_use]
pub fn manager_handler_keys() -> impl ExactSizeIterator<Item = (OperationScopeKind, &'static str)> {
    super::registry::operation_entries()
        .iter()
        .map(|entry| (entry.scope_kind, entry.spec.name))
}

#[must_use]
pub fn dispatch_operation(
    services: &ManagerServices,
    request: &OperationRequest,
) -> OperationResponse {
    operation_entry(request.scope.kind(), &request.op)
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
    if request.scope.kind() == OperationScopeKind::System && request.op == CREATE_SANDBOX_SPEC.name
    {
        return super::registry::management_operations::dispatch_create_sandbox_with_progress(
            services, request, &progress,
        );
    }
    dispatch_operation(services, request)
}

fn operation_entry(
    scope_kind: OperationScopeKind,
    operation: &str,
) -> Option<&'static ManagerOperationEntry> {
    super::registry::operation_entries()
        .iter()
        .find(|entry| entry.scope_kind == scope_kind && entry.spec.name == operation)
}
