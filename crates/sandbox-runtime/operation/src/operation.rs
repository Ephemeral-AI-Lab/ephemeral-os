use crate::internal::services::SandboxRuntimeOperations;

pub use sandbox_protocol::{
    ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationCatalog, OperationExecutionSpace,
    OperationFamilySpec, OperationSpec,
};

#[derive(Clone, Copy)]
pub(crate) struct OperationEntry {
    pub(crate) spec: &'static OperationSpec,
    pub(crate) dispatch:
        fn(&SandboxRuntimeOperations, &sandbox_protocol::Request) -> sandbox_protocol::Response,
}

impl OperationEntry {
    #[must_use]
    pub(crate) const fn new(
        spec: &'static OperationSpec,
        dispatch: fn(
            &SandboxRuntimeOperations,
            &sandbox_protocol::Request,
        ) -> sandbox_protocol::Response,
    ) -> Self {
        Self { spec, dispatch }
    }
}
