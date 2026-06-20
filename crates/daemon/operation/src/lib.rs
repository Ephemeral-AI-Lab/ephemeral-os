#![forbid(unsafe_code)]

pub(crate) extern crate workspace as workspace_crate;

mod internal;
pub mod operation;
mod public;

pub use internal::{error, services, workspace_remount, workspace_session};
pub use operation::{
    ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationEntry, OperationFamily, OperationRequest,
    OperationResponse, OperationSpec,
};
pub use public::command;
pub use public::protocol;

pub use command::CommandOperationService;
pub use error::OperationServiceError;
pub use services::DaemonOperations;

#[must_use]
pub fn operation_specs() -> &'static [&'static OperationSpec] {
    public::operation_specs()
}

#[must_use]
pub fn dispatch_operation(
    operations: &DaemonOperations,
    request: OperationRequest<'_>,
) -> OperationResponse {
    public::dispatch_operation(operations, request)
}
