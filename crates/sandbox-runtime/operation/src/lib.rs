#![forbid(unsafe_code)]

pub(crate) extern crate workspace as workspace_crate;

mod internal;
pub mod operation;
mod public;

pub use internal::{workspace_remount, workspace_session};
pub use operation::{
    ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationAuthority, OperationCatalog, OperationEntry,
    OperationFamily, OperationRequest, OperationResponse, OperationSpec,
};
pub use public::command;

pub use command::CommandOperationService;
pub use internal::services::SandboxDaemonOperations;

#[must_use]
pub fn operation_specs() -> &'static [&'static OperationSpec] {
    public::operation_specs()
}

#[must_use]
pub fn operation_catalog() -> OperationCatalog {
    OperationCatalog::new(OperationAuthority::SandboxDaemon, operation_specs())
}

#[must_use]
pub fn dispatch_operation(
    operations: &SandboxDaemonOperations,
    request: OperationRequest<'_>,
) -> OperationResponse {
    public::dispatch_operation(operations, request)
}
