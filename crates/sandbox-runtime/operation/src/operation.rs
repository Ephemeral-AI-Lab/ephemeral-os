use std::sync::OnceLock;
use std::time::Instant;

use crate::services::SandboxRuntimeOperations;
use crate::workspace_crate::{RuntimeMetricStatus, RuntimeOperationName};
use crate::{command, layerstack};

pub use sandbox_protocol::{
    ArgCliSpec, ArgKind, ArgSpec, CliOperationCatalog, CliOperationExecutionSpace,
    CliOperationFamilySpec, CliOperationSpec, CliSpec,
};

#[derive(Clone, Copy)]
pub(crate) struct OperationEntry {
    pub(crate) name: &'static str,
    pub(crate) cli: Option<&'static CliOperationSpec>,
    pub(crate) dispatch:
        fn(&SandboxRuntimeOperations, &sandbox_protocol::Request) -> sandbox_protocol::Response,
}

impl OperationEntry {
    #[must_use]
    pub(crate) const fn cli(
        spec: &'static CliOperationSpec,
        dispatch: fn(
            &SandboxRuntimeOperations,
            &sandbox_protocol::Request,
        ) -> sandbox_protocol::Response,
    ) -> Self {
        Self {
            name: spec.name,
            cli: Some(spec),
            dispatch,
        }
    }

    #[must_use]
    const fn cli_spec(self) -> Option<&'static CliOperationSpec> {
        self.cli
    }
}

const CLI_FAMILIES: &[&CliOperationFamilySpec] =
    &[&command::COMMAND_FAMILY, &layerstack::LAYERSTACK_FAMILY];
static CLI_SPECS: OnceLock<&'static [&'static CliOperationSpec]> = OnceLock::new();

pub(crate) fn cli_operation_families() -> &'static [&'static CliOperationFamilySpec] {
    CLI_FAMILIES
}

pub(crate) fn cli_operation_specs() -> &'static [&'static CliOperationSpec] {
    CLI_SPECS.get_or_init(|| {
        Box::leak(
            operation_entry_groups()
                .into_iter()
                .flat_map(|entries| entries.iter())
                .filter_map(|entry| entry.cli_spec())
                .collect::<Vec<_>>()
                .into_boxed_slice(),
        )
    })
}

pub(crate) fn dispatch_operation(
    operations: &SandboxRuntimeOperations,
    request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    operation_entry_groups()
        .into_iter()
        .flat_map(|entries| entries.iter())
        .find(|entry| entry.name == request.op)
        .map_or_else(sandbox_protocol::Response::unknown_op, |entry| {
            let started = Instant::now();
            let response = (entry.dispatch)(operations, request);
            operations.metrics().record_runtime_latency(
                RuntimeOperationName::from_static_name(entry.name),
                response_status(&response),
                started.elapsed(),
            );
            response
        })
}

pub(crate) fn known_operation_name(operation: &str) -> Option<&'static str> {
    operation_entry_groups()
        .into_iter()
        .flat_map(|entries| entries.iter())
        .find_map(|entry| (entry.name == operation).then_some(entry.name))
}

fn operation_entry_groups() -> [&'static [OperationEntry]; 2] {
    [
        command::operation_entries(),
        layerstack::operation_entries(),
    ]
}

fn response_status(response: &sandbox_protocol::Response) -> RuntimeMetricStatus {
    let value = response.clone().into_json_value();
    if value.get("error").is_some() {
        RuntimeMetricStatus::Error
    } else {
        RuntimeMetricStatus::Ok
    }
}
