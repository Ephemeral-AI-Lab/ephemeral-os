use sandbox_protocol::{
    CliOperationCatalog, CliOperationExecutionSpace, CliOperationFamilySpec, CliOperationSpec,
};

use super::cli_definition;

#[must_use]
pub const fn cli_operation_families() -> &'static [&'static CliOperationFamilySpec] {
    cli_definition::cli_operation_families()
}

#[must_use]
pub const fn cli_operation_specs() -> &'static [&'static CliOperationSpec] {
    cli_definition::cli_operation_specs()
}

#[must_use]
pub const fn cli_operation_catalog() -> CliOperationCatalog {
    CliOperationCatalog::new(
        CliOperationExecutionSpace::Manager,
        cli_operation_families(),
        cli_operation_specs(),
    )
}
