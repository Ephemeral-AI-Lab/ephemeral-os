//! Runtime CLI operation surface (the `runtime` execution space).
//!
//! This crate is **spec-only**: it owns the `CliOperationSpec` catalog for the
//! runtime execution space and nothing else. `OperationEntry` registrations and
//! dispatch fn-pointers live in `sandbox-runtime`, which imports these specs.
//! Keeping the catalog in a thin, dependency-light crate lets protocol clients
//! link the runtime operation surface without pulling in the runtime engine.
#![forbid(unsafe_code)]

mod command;
mod file;

pub use command::{COMMAND_FAMILY, EXEC_COMMAND_SPEC, READ_LINES_SPEC, WRITE_STDIN_SPEC};
pub use file::{
    FILE_BLAME_SPEC, FILE_EDIT_SPEC, FILE_FAMILY, FILE_LIST_SPEC, FILE_READ_SPEC, FILE_WRITE_SPEC,
};

use sandbox_protocol::{
    CliOperationCatalog, CliOperationExecutionSpace, CliOperationFamilySpec, CliOperationSpec,
};

const FAMILIES: &[&CliOperationFamilySpec] = &[&COMMAND_FAMILY, &FILE_FAMILY];

const SPECS: &[&CliOperationSpec] = &[
    &EXEC_COMMAND_SPEC,
    &WRITE_STDIN_SPEC,
    &READ_LINES_SPEC,
    &FILE_READ_SPEC,
    &FILE_WRITE_SPEC,
    &FILE_EDIT_SPEC,
    &FILE_BLAME_SPEC,
];

#[must_use]
pub const fn cli_operation_families() -> &'static [&'static CliOperationFamilySpec] {
    FAMILIES
}

#[must_use]
pub const fn cli_operation_specs() -> &'static [&'static CliOperationSpec] {
    SPECS
}

#[must_use]
pub const fn runtime_catalog() -> CliOperationCatalog {
    CliOperationCatalog::new(CliOperationExecutionSpace::Runtime, FAMILIES, SPECS)
}
