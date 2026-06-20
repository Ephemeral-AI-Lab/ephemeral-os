pub mod command;

use crate::internal::services::SandboxRuntimeOperations;
use crate::operation::OperationSpec;

pub(crate) fn operation_specs() -> &'static [&'static OperationSpec] {
    command::operation_specs()
}

pub(crate) fn dispatch_operation(
    operations: &SandboxRuntimeOperations,
    request: sandbox_protocol::OperationRequest<'_>,
) -> sandbox_protocol::OperationResponse {
    command::operation_entries()
        .iter()
        .find(|entry| entry.spec.name == request.name)
        .map_or_else(
            || sandbox_protocol::OperationResponse::unknown_op(&request),
            |entry| (entry.dispatch)(operations, request),
        )
}
