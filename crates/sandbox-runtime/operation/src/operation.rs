use crate::internal::services::SandboxRuntimeOperations;

pub use sandbox_protocol::{
    ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationCatalog, OperationExecutionSpace,
    OperationFamily, OperationSpec,
};

#[derive(Clone, Copy)]
pub struct OperationEntry {
    pub spec: &'static OperationSpec,
    pub dispatch: fn(
        &SandboxRuntimeOperations,
        sandbox_protocol::OperationRequest<'_>,
    ) -> sandbox_protocol::OperationResponse,
}

impl OperationEntry {
    #[must_use]
    pub const fn new(
        spec: &'static OperationSpec,
        dispatch: fn(
            &SandboxRuntimeOperations,
            sandbox_protocol::OperationRequest<'_>,
        ) -> sandbox_protocol::OperationResponse,
    ) -> Self {
        Self { spec, dispatch }
    }
}
