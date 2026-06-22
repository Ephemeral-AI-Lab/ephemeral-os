use sandbox_protocol::{
    CliOperationCatalog, CliOperationExecutionSpace, CliOperationFamilySpec, CliOperationSpec,
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
        CliOperationExecutionSpace::Manager,
        cli_operation_families(),
        cli_operation_specs(),
    )
}
