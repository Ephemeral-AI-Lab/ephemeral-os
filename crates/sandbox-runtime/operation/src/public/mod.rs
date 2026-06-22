pub mod cgroup_monitor;
pub mod command;

use std::sync::OnceLock;

use crate::internal::services::SandboxRuntimeOperations;
use crate::operation::{CliOperationFamilySpec, CliOperationSpec};

pub(crate) fn cli_operation_families() -> &'static [&'static CliOperationFamilySpec] {
    static FAMILIES: OnceLock<Box<[&'static CliOperationFamilySpec]>> = OnceLock::new();
    FAMILIES
        .get_or_init(|| {
            command::cli_operation_families()
                .iter()
                .chain(cgroup_monitor::cli_operation_families().iter())
                .copied()
                .collect::<Vec<_>>()
                .into_boxed_slice()
        })
        .as_ref()
}

pub(crate) fn cli_operation_specs() -> &'static [&'static CliOperationSpec] {
    static SPECS: OnceLock<Box<[&'static CliOperationSpec]>> = OnceLock::new();
    SPECS
        .get_or_init(|| {
            command::cli_operation_specs()
                .iter()
                .chain(cgroup_monitor::cli_operation_specs().iter())
                .copied()
                .collect::<Vec<_>>()
                .into_boxed_slice()
        })
        .as_ref()
}

pub(crate) fn dispatch_operation(
    operations: &SandboxRuntimeOperations,
    request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    command::operation_entries()
        .iter()
        .chain(cgroup_monitor::operation_entries().iter())
        .find(|entry| entry.spec.name == request.op)
        .map_or_else(sandbox_protocol::Response::unknown_op, |entry| {
            (entry.dispatch)(operations, request)
        })
}
