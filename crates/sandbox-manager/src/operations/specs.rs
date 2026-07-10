use sandbox_operation_contract::{OperationCatalog, OperationFamilySpec, OperationSpec};

#[must_use]
pub const fn operation_families() -> &'static [&'static OperationFamilySpec] {
    sandbox_operation_catalog::manager::operation_families()
}

#[must_use]
pub const fn operation_specs() -> &'static [&'static OperationSpec] {
    sandbox_operation_catalog::manager::operation_specs()
}

#[must_use]
pub const fn operation_catalog() -> OperationCatalog {
    sandbox_operation_catalog::manager::manager_catalog()
}
