use sandbox_operation_contract::{OperationCatalog, OperationFamilySpec, OperationSpec};

#[must_use]
pub const fn operation_families() -> &'static [&'static OperationFamilySpec] {
    sandbox_manager_operations::operation_families()
}

#[must_use]
pub const fn operation_specs() -> &'static [&'static OperationSpec] {
    sandbox_manager_operations::operation_specs()
}

#[must_use]
pub const fn operation_catalog() -> OperationCatalog {
    sandbox_manager_operations::manager_catalog()
}
