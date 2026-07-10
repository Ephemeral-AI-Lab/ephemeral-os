//! Runtime operation catalog.

mod command;
mod file;

pub use command::{COMMAND_FAMILY, EXEC_COMMAND_SPEC, READ_LINES_SPEC, WRITE_STDIN_SPEC};
pub use file::{FILE_BLAME_SPEC, FILE_EDIT_SPEC, FILE_FAMILY, FILE_READ_SPEC, FILE_WRITE_SPEC};

use sandbox_operation_contract::{
    OperationCatalog, OperationDomain, OperationFamilySpec, OperationRouteSpec, OperationSpec,
};

use crate::routed::{self, RoutedOperation};

const FAMILIES: &[&OperationFamilySpec] = &[&COMMAND_FAMILY, &FILE_FAMILY];

const OPERATIONS: &[&RoutedOperation] = &[
    &command::EXEC_COMMAND,
    &command::WRITE_STDIN,
    &command::READ_LINES,
    &file::FILE_READ,
    &file::FILE_WRITE,
    &file::FILE_EDIT,
    &file::FILE_BLAME,
];

const SPECS: [&OperationSpec; OPERATIONS.len()] = routed::specs(OPERATIONS);
const ROUTES: [OperationRouteSpec; routed::route_count(OPERATIONS)] =
    routed::expand_routes(OPERATIONS);

#[must_use]
pub const fn operation_families() -> &'static [&'static OperationFamilySpec] {
    FAMILIES
}

#[must_use]
pub const fn operation_specs() -> &'static [&'static OperationSpec] {
    &SPECS
}

pub(crate) const fn routes() -> &'static [OperationRouteSpec] {
    &ROUTES
}

#[must_use]
pub const fn runtime_catalog() -> OperationCatalog {
    OperationCatalog::new(OperationDomain::Runtime, FAMILIES, &SPECS, &ROUTES)
}
