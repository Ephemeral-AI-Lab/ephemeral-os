//! Public route views over the per-domain `RoutedOperation` declarations.

#[cfg(any(feature = "manager", feature = "runtime", feature = "observability"))]
use sandbox_operation_contract::OperationRouteSpec;

#[cfg(feature = "manager")]
#[must_use]
pub const fn manager_routes() -> &'static [OperationRouteSpec] {
    crate::manager::routes()
}

#[cfg(feature = "runtime")]
#[must_use]
pub const fn runtime_routes() -> &'static [OperationRouteSpec] {
    crate::runtime::routes()
}

#[cfg(feature = "observability")]
#[must_use]
pub const fn observability_routes() -> &'static [OperationRouteSpec] {
    crate::observability::routes()
}

#[cfg(all(feature = "manager", feature = "runtime", feature = "observability"))]
pub fn public_routes() -> impl Iterator<Item = &'static OperationRouteSpec> {
    [manager_routes(), runtime_routes(), observability_routes()]
        .into_iter()
        .flatten()
}
