pub mod command;

use crate::internal::services::SandboxRuntimeOperations;
use crate::operation::OperationSpec;

pub(crate) fn operation_specs() -> &'static [&'static OperationSpec] {
    command::operation_specs()
}

pub(crate) fn dispatch_operation(
    operations: &SandboxRuntimeOperations,
    request: sandbox_protocol::Request<'_>,
) -> sandbox_protocol::Response {
    command::operation_entries()
        .iter()
        .find(|entry| entry.spec.name == request.name)
        .map_or_else(
            || sandbox_protocol::Response::unknown_op(&request),
            |entry| (entry.dispatch)(operations, request),
        )
}
