use crate::internal::services::SandboxDaemonOperations;

pub use sandbox_protocol::{
    ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationAuthority, OperationCatalog, OperationFamily,
    OperationSpec,
};

pub type OperationRequest<'a> = sandbox_protocol::Request<'a>;
pub type OperationResponse = sandbox_protocol::SandboxResponse;

pub type OperationDispatch =
    fn(&SandboxDaemonOperations, OperationRequest<'_>) -> OperationResponse;

#[derive(Clone, Copy)]
pub struct OperationEntry {
    pub spec: &'static OperationSpec,
    pub dispatch: OperationDispatch,
}

impl OperationEntry {
    #[must_use]
    pub const fn new(spec: &'static OperationSpec, dispatch: OperationDispatch) -> Self {
        Self { spec, dispatch }
    }
}
