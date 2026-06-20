pub mod command;

use crate::internal::services::SandboxDaemonOperations;
use crate::operation::{OperationRequest, OperationResponse, OperationSpec};

pub(crate) fn operation_specs() -> &'static [&'static OperationSpec] {
    command::operation_specs()
}

pub(crate) fn dispatch_operation(
    operations: &SandboxDaemonOperations,
    request: OperationRequest<'_>,
) -> OperationResponse {
    command::operation_entries()
        .iter()
        .find(|entry| entry.spec.name == request.name)
        .map_or_else(
            || OperationResponse::unknown_op(&request),
            |entry| (entry.dispatch)(operations, request),
        )
}
