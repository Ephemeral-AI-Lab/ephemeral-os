pub mod command;

use crate::internal::services::SandboxRuntimeOperations;
use crate::operation::{OperationFamilySpec, OperationSpec};

pub(crate) fn operation_families() -> &'static [&'static OperationFamilySpec] {
    command::operation_families()
}

pub(crate) fn operation_specs() -> &'static [&'static OperationSpec] {
    command::operation_specs()
}

pub(crate) fn dispatch_operation(
    operations: &SandboxRuntimeOperations,
    request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    command::operation_entries()
        .iter()
        .find(|entry| entry.spec.name == request.op)
        .map_or_else(sandbox_protocol::Response::unknown_op, |entry| {
            (entry.dispatch)(operations, request)
        })
}
