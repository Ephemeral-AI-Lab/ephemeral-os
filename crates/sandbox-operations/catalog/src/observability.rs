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
    ArgKind, ArgSpec, OperationCatalog, OperationDomain, OperationFamilySpec, OperationSpec,
};

use crate::routes;

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
const SPECS: &[&OperationSpec] = &[
    &SNAPSHOT_SPEC,
    &trace::TRACE_SPEC,
    &events::EVENTS_SPEC,
    &cgroup::CGROUP_SPEC,
    &layerstack::LAYERSTACK_SPEC,
];

#[must_use]
pub const fn observability_catalog() -> OperationCatalog {
    OperationCatalog::new(
        OperationDomain::Observability,
        FAMILIES,
        SPECS,
        routes::observability_routes(),
    )
}
