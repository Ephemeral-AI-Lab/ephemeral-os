//! Observability operation declarations.
mod cgroup;
mod events;
mod layerstack;
mod snapshot;
mod trace;

pub use cgroup::CGROUP_SPEC;
pub use events::EVENTS_SPEC;
pub use layerstack::LAYERSTACK_SPEC;
pub use snapshot::SNAPSHOT_SPEC;
pub use trace::TRACE_SPEC;

use sandbox_operation_contract::{
    ArgKind, ArgSpec, OperationCatalog, OperationDomain, OperationFamilySpec, OperationRouteSpec,
    OperationSpec,
};

use crate::routed::{self, RoutedOperation};

const OBSERVABILITY_FAMILY: OperationFamilySpec = OperationFamilySpec {
    id: "observability",
    title: "Observability",
    summary: "Inspect traces, events, and resource stats for a sandbox.",
    description: "Read a sandbox's observability stream — span waterfalls, domain \
events, cgroup/disk resource series, and live state. Snapshot can also \
aggregate ready manager-known sandboxes when --sandbox-id is omitted.",
};

pub(crate) const SANDBOX_ID_ARG: ArgSpec = ArgSpec::required(
    "sandbox_id",
    ArgKind::String,
    "Target sandbox id (selects the daemon to query).",
);

const FAMILIES: &[&OperationFamilySpec] = &[&OBSERVABILITY_FAMILY];

const OPERATIONS: &[&RoutedOperation] = &[
    &snapshot::SNAPSHOT,
    &trace::TRACE,
    &events::EVENTS,
    &cgroup::CGROUP,
    &layerstack::LAYERSTACK,
];

const SPECS: [&OperationSpec; OPERATIONS.len()] = routed::specs(OPERATIONS);
const ROUTES: [OperationRouteSpec; routed::route_count(OPERATIONS)] =
    routed::expand_routes(OPERATIONS);

pub(crate) const fn routes() -> &'static [OperationRouteSpec] {
    &ROUTES
}

#[must_use]
pub const fn observability_catalog() -> OperationCatalog {
    OperationCatalog::new(OperationDomain::Observability, FAMILIES, &SPECS, &ROUTES)
}
