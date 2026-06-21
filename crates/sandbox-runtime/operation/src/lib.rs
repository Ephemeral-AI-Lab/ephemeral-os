#![forbid(unsafe_code)]

pub(crate) extern crate sandbox_runtime_workspace as workspace_crate;

mod internal;
pub mod operation;
mod public;

pub use internal::{workspace_remount, workspace_session};
pub use operation::{
    ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationCatalog, OperationEntry,
    OperationExecutionSpace, OperationFamily, OperationSpec,
};
pub use public::command;

pub use command::CommandOperationService;
pub use internal::services::SandboxRuntimeOperations;

#[must_use]
pub fn operation_specs() -> &'static [&'static OperationSpec] {
    public::operation_specs()
}

#[must_use]
pub fn operation_catalog() -> OperationCatalog {
    OperationCatalog::new(OperationExecutionSpace::Runtime, operation_specs())
}

#[must_use]
pub fn dispatch_operation(
    operations: &SandboxRuntimeOperations,
    request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    public::dispatch_operation(operations, request)
}
