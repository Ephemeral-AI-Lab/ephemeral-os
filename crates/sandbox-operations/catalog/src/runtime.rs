//! Runtime operation catalog.

mod command;
mod file;

pub use command::{COMMAND_FAMILY, EXEC_COMMAND_SPEC, READ_LINES_SPEC, WRITE_STDIN_SPEC};
pub use file::{FILE_BLAME_SPEC, FILE_EDIT_SPEC, FILE_FAMILY, FILE_READ_SPEC, FILE_WRITE_SPEC};

use sandbox_operation_contract::{
    OperationCatalog, OperationDomain, OperationFamilySpec, OperationRouteSpec, OperationSpec,
};

use crate::routed::{self, RoutedOperation};

/// Capability taxonomy is broader than the public CLI operation list.  These
/// leaves are deliberately catalog-only: tests can describe the actual
/// boundary they exercise without inventing a second Python taxonomy or
/// pretending that an internal capability is a public operation.
const DAEMON_HTTP_FAMILY: OperationFamilySpec = OperationFamilySpec {
    id: "daemon_http",
    title: "Daemon HTTP",
    summary: "Daemon HTTP capability boundary.",
    description: "Runtime capability exercised through the daemon HTTP boundary.",
};

const NETWORK_ISOLATION_FAMILY: OperationFamilySpec = OperationFamilySpec {
    id: "network_isolation",
    title: "Network isolation",
    summary: "Runtime network-isolation capability.",
    description: "Runtime capability that constrains sandbox network access.",
};

const RESERVED_PATHS_FAMILY: OperationFamilySpec = OperationFamilySpec {
    id: "reserved_paths",
    title: "Reserved paths",
    summary: "Reserved workspace-path capability.",
    description: "Runtime capability that protects reserved workspace paths.",
};

const SHELL_SECURITY_FAMILY: OperationFamilySpec = OperationFamilySpec {
    id: "shell_security",
    title: "Shell security",
    summary: "Shell-security capability.",
    description: "Runtime capability that enforces shell execution policy.",
};

const WORKSPACE_SESSION_FAMILY: OperationFamilySpec = OperationFamilySpec {
    id: "workspace_session",
    title: "Workspace session",
    summary: "Workspace-session lifecycle capability.",
    description: "Runtime capability that owns workspace-session lifecycle and finalization.",
};

const FAMILIES: &[&OperationFamilySpec] = &[
    &COMMAND_FAMILY,
    &FILE_FAMILY,
    &DAEMON_HTTP_FAMILY,
    &NETWORK_ISOLATION_FAMILY,
    &RESERVED_PATHS_FAMILY,
    &SHELL_SECURITY_FAMILY,
    &WORKSPACE_SESSION_FAMILY,
];

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
