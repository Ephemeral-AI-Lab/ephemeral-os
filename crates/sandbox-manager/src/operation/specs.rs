use sandbox_protocol::{CliOperationCatalog, CliOperationFamilySpec, CliOperationSpec};

#[must_use]
pub const fn cli_operation_families() -> &'static [&'static CliOperationFamilySpec] {
    sandbox_manager_operations::cli_operation_families()
}

#[must_use]
pub const fn cli_operation_specs() -> &'static [&'static CliOperationSpec] {
    sandbox_manager_operations::cli_operation_specs()
}

#[must_use]
pub const fn cli_operation_catalog() -> CliOperationCatalog {
    sandbox_manager_operations::manager_catalog()
}
