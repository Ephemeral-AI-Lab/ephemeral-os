use crate::OperationSpec;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OperationExecutionSpace {
    Manager,
    Runtime,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OperationCatalog {
    pub operation_space: OperationExecutionSpace,
    pub operations: &'static [&'static OperationSpec],
}

impl OperationCatalog {
    #[must_use]
    pub const fn new(
        operation_space: OperationExecutionSpace,
        operations: &'static [&'static OperationSpec],
    ) -> Self {
        Self {
            operation_space,
            operations,
        }
    }
}
