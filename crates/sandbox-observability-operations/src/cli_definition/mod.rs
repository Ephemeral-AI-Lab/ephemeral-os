//! Adapter layer: the `CliOperationSpec` catalog for the `observability`
//! execution space. One module per operation owns that operation's spec. No
//! per-operation dispatch code lives here because sandbox-scoped views resolve
//! to the daemon op `get_observability`; aggregate `snapshot` routing is handled
//! by the gateway and manager.
mod cgroup;
mod events;
mod layerstack;
mod snapshot;
mod trace;

pub use snapshot::SNAPSHOT_SPEC;

use sandbox_protocol::{
    ArgCliSpec, ArgKind, ArgSpec, CliOperationCatalog, CliOperationExecutionSpace,
    CliOperationFamilySpec, CliOperationSpec,
};

const OBSERVABILITY_FAMILY: CliOperationFamilySpec = CliOperationFamilySpec {
    id: "observability",
    title: "Observability",
    summary: "Inspect traces, events, and resource stats for a sandbox.",
    description: "Read a sandbox's observability stream — span waterfalls, domain \
events, cgroup/disk resource series, and live state. Snapshot can also \
aggregate ready manager-known sandboxes when --sandbox-id is omitted.",
};

/// Shared `--sandbox-id` selector for observability operations that must target
/// one sandbox's daemon.
pub(crate) const SANDBOX_ID_ARG: ArgSpec = ArgSpec::required(
    "sandbox_id",
    ArgKind::String,
    "Target sandbox id (selects the daemon to query).",
    Some(ArgCliSpec {
        flag: Some("--sandbox-id"),
        positional: None,
    }),
);

const FAMILIES: &[&CliOperationFamilySpec] = &[&OBSERVABILITY_FAMILY];
const SPECS: &[&CliOperationSpec] = &[
    &SNAPSHOT_SPEC,
    &trace::TRACE_SPEC,
    &events::EVENTS_SPEC,
    &cgroup::CGROUP_SPEC,
    &layerstack::LAYERSTACK_SPEC,
];

#[must_use]
pub fn observability_catalog() -> CliOperationCatalog {
    CliOperationCatalog::new(CliOperationExecutionSpace::Observability, FAMILIES, SPECS)
}
