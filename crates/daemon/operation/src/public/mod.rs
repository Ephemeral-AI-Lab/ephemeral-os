pub mod command;
pub mod protocol;

use crate::internal::services::DaemonOperations;
use crate::operation::OperationSpec;
use protocol::{OperationRequest, OperationResponse};

pub(crate) fn operation_specs() -> &'static [&'static OperationSpec] {
    command::operation_specs()
}

pub(crate) fn dispatch_operation(
    operations: &DaemonOperations,
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
