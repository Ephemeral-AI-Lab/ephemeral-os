//! Runtime operation catalog.

mod command;
mod file;

pub use command::{COMMAND_FAMILY, EXEC_COMMAND_SPEC, READ_LINES_SPEC, WRITE_STDIN_SPEC};
pub use file::{FILE_BLAME_SPEC, FILE_EDIT_SPEC, FILE_FAMILY, FILE_READ_SPEC, FILE_WRITE_SPEC};

use sandbox_operation_contract::{
    OperationCatalog, OperationDomain, OperationFamilySpec, OperationSpec,
};

use crate::routes;

const FAMILIES: &[&OperationFamilySpec] = &[&COMMAND_FAMILY, &FILE_FAMILY];

const SPECS: &[&OperationSpec] = &[
    &EXEC_COMMAND_SPEC,
    &WRITE_STDIN_SPEC,
    &READ_LINES_SPEC,
    &FILE_READ_SPEC,
    &FILE_WRITE_SPEC,
    &FILE_EDIT_SPEC,
    &FILE_BLAME_SPEC,
];

#[must_use]
pub const fn operation_families() -> &'static [&'static OperationFamilySpec] {
    FAMILIES
}

#[must_use]
pub const fn operation_specs() -> &'static [&'static OperationSpec] {
    SPECS
}

#[must_use]
pub const fn runtime_catalog() -> OperationCatalog {
    OperationCatalog::new(
        OperationDomain::Runtime,
        FAMILIES,
        SPECS,
        routes::runtime_routes(),
    )
}
