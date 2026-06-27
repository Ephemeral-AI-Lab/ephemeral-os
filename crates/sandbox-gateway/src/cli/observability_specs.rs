//! CLI operation specs for the read-only `observability` execution space.
//!
//! Every operation resolves to the single daemon op `get_observability`; the
//! operation name is the `view` value (see `request_builder`). This slice ships
//! the `layerstack` view; sibling views (`cgroup`, `snapshot`, …) land with their
//! daemon bodies.

use sandbox_protocol::{
    ArgCliSpec, ArgKind, ArgSpec, CliOperationCatalog, CliOperationExecutionSpace,
    CliOperationFamilySpec, CliOperationSpec, CliSpec,
};

pub const OBSERVABILITY_FAMILY: CliOperationFamilySpec = CliOperationFamilySpec {
    id: "observability",
    title: "Observability",
    summary: "Inspect traces, events, and resource stats for a sandbox.",
    description: "Read a sandbox's observability stream — span waterfalls, domain \
events, cgroup/disk resource series, and live state, over the daemon \
get_observability op.",
};

const SANDBOX_ID_ARG: ArgSpec = ArgSpec::required(
    "sandbox_id",
    ArgKind::String,
    "Target sandbox id (selects the daemon to query).",
    Some(ArgCliSpec {
        flag: Some("--sandbox-id"),
        positional: None,
    }),
);

const LAYERSTACK_SPEC: CliOperationSpec = CliOperationSpec {
    name: "layerstack",
    family: "observability",
    summary: "Per-layer leasing/booking inventory, and stack series.",
    description: "Show the active manifest as a per-layer inventory: disk bytes, \
how many workspaces lease each layer, and which leased layers book each base. \
Served live from the runtime; does not read the log.",
    args: &[
        SANDBOX_ID_ARG,
        ArgSpec::optional(
            "workspace",
            ArgKind::String,
            "Restrict to one workspace/session: its mounted layers and which other sessions share each.",
            None,
            Some(ArgCliSpec {
                flag: Some("--workspace"),
                positional: None,
            }),
        ),
        ArgSpec::optional(
            "window_ms",
            ArgKind::Integer,
            "Lookback window in milliseconds for the stack trend (max 600000).",
            Some("60000"),
            Some(ArgCliSpec {
                flag: Some("--window-ms"),
                positional: None,
            }),
        ),
    ],
    cli: Some(CliSpec {
        path: &["observability", "layerstack"],
        usage: "sandbox-cli observability layerstack --sandbox-id ID [--workspace WS] [--window-ms MS]",
        examples: &[
            "sandbox-cli observability layerstack --sandbox-id eos-abc",
            "sandbox-cli observability layerstack --sandbox-id eos-abc --workspace ws-7",
        ],
    }),
    related: &[],
};

const FAMILIES: &[&CliOperationFamilySpec] = &[&OBSERVABILITY_FAMILY];
const SPECS: &[&CliOperationSpec] = &[&LAYERSTACK_SPEC];

#[must_use]
pub fn observability_catalog() -> CliOperationCatalog {
    CliOperationCatalog::new(CliOperationExecutionSpace::Observability, FAMILIES, SPECS)
}
