use sandbox_protocol::{
    CliOperationCatalog, CliOperationFamilySpec, CliOperationSpec, OperationExecutionSpace,
};

use super::impls;

#[must_use]
pub const fn cli_operation_families() -> &'static [&'static CliOperationFamilySpec] {
    impls::cli_operation_families()
}

#[must_use]
pub const fn cli_operation_specs() -> &'static [&'static CliOperationSpec] {
    impls::cli_operation_specs()
}

#[must_use]
pub const fn cli_operation_catalog() -> CliOperationCatalog {
    CliOperationCatalog::new(
        OperationExecutionSpace::Manager,
        cli_operation_families(),
        cli_operation_specs(),
    )
}
