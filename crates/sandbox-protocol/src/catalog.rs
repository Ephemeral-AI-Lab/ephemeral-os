use crate::OperationSpec;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OperationExecutionSpace {
    Manager,
    Runtime,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OperationCatalog {
    pub operation_execution_space: OperationExecutionSpace,
    pub operations: &'static [&'static OperationSpec],
}

impl OperationCatalog {
    #[must_use]
    pub const fn new(
        operation_execution_space: OperationExecutionSpace,
        operations: &'static [&'static OperationSpec],
    ) -> Self {
        Self {
            operation_execution_space,
            operations,
        }
    }
}
