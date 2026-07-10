use crate::operation_adapter::{command_operations, file_operations, workspace_session_operations};
use crate::services::SandboxRuntimeOperations;
use sandbox_protocol::CliOperationSpec;

#[derive(Clone, Copy)]
pub(crate) struct OperationEntry {
    pub(crate) name: &'static str,
    pub(crate) cli: Option<&'static CliOperationSpec>,
    pub(crate) dispatch: OperationDispatch,
}

type OperationDispatch =
    fn(&SandboxRuntimeOperations, &sandbox_protocol::Request) -> sandbox_protocol::Response;

impl OperationEntry {
    #[must_use]
    pub(crate) const fn cli(spec: &'static CliOperationSpec, dispatch: OperationDispatch) -> Self {
        Self {
            name: spec.name,
            cli: Some(spec),
            dispatch,
        }
    }
}

pub(crate) fn dispatch_operation(
    operations: &SandboxRuntimeOperations,
    request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    operation_entry_groups()
        .iter()
        .flat_map(|entries| entries.iter())
        .find(|entry| entry.name == request.op)
        .map_or_else(sandbox_protocol::Response::unknown_op, |entry| {
            debug_assert!(entry.cli.is_none_or(|spec| spec.name == entry.name));
            (entry.dispatch)(operations, request)
        })
}

pub(crate) fn known_operation_name(operation: &str) -> Option<&'static str> {
    operation_entry_groups()
        .iter()
        .flat_map(|entries| entries.iter())
        .find_map(|entry| (entry.name == operation).then_some(entry.name))
}

const OPERATION_ENTRY_GROUPS: &[&[OperationEntry]] = &[
    command_operations::operation_entries(),
    file_operations::operation_entries(),
    workspace_session_operations::operation_entries(),
    crate::layerstack::squash_operation_entries(),
    crate::layerstack::export_operation_entries(),
];

fn operation_entry_groups() -> &'static [&'static [OperationEntry]] {
    OPERATION_ENTRY_GROUPS
}
