//! Manager operation catalog.

mod management;

pub use management::{
    CREATE_SANDBOX, CREATE_SANDBOX_SPEC, DESTROY_SANDBOX, DESTROY_SANDBOX_SPEC, EXPORT_CHANGES,
    EXPORT_CHANGES_SPEC, INSPECT_SANDBOX, INSPECT_SANDBOX_SPEC, LIST_DOCKER_IMAGES,
    LIST_DOCKER_IMAGES_SPEC, LIST_SANDBOXES, LIST_SANDBOXES_SPEC, LIST_WORKSPACE_DIRECTORIES,
    LIST_WORKSPACE_DIRECTORIES_SPEC, MANAGEMENT_FAMILY, SQUASH_LAYERSTACKS,
    SQUASH_LAYERSTACKS_SPEC,
};

use sandbox_operation_contract::{
    OperationCatalog, OperationDomain, OperationFamilySpec, OperationRouteSpec, OperationSpec,
};

use crate::routed::{self, RoutedOperation};

const FAMILIES: &[&OperationFamilySpec] = &[&MANAGEMENT_FAMILY];

const OPERATIONS: &[&RoutedOperation] = &[
    &management::CREATE_SANDBOX,
    &management::LIST_DOCKER_IMAGES,
    &management::LIST_WORKSPACE_DIRECTORIES,
    &management::DESTROY_SANDBOX,
    &management::LIST_SANDBOXES,
    &management::INSPECT_SANDBOX,
    &management::SQUASH_LAYERSTACKS,
    &management::EXPORT_CHANGES,
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
pub const fn manager_catalog() -> OperationCatalog {
    OperationCatalog::new(OperationDomain::Manager, FAMILIES, &SPECS, &ROUTES)
}
