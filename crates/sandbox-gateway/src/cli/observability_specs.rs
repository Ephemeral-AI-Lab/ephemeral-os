//! CLI operation specs for the read-only `observability` execution space.
//!
//! Every operation resolves to the single daemon op `get_observability`; the
//! operation name is the `view` value and the flags map to that op's params (see
//! `request_builder`). One `CliOperationSpec` per view gives each its own
//! subcommand, help page, and args.

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

const SNAPSHOT_SPEC: CliOperationSpec = CliOperationSpec {
    name: "snapshot",
    family: "observability",
    summary: "Show live sandbox state.",
    description: "Show current state from the runtime registry: sandbox lifecycle \
state, workspaces (with layer counts), in-flight executions, and the latest \
resource sample per scope. Served live; does not read the log.",
    args: &[SANDBOX_ID_ARG],
    cli: Some(CliSpec {
        path: &["observability", "snapshot"],
        usage: "sandbox-cli observability snapshot --sandbox-id ID",
        examples: &["sandbox-cli observability snapshot --sandbox-id eos-abc"],
    }),
    related: &["trace", "cgroup"],
};

const TRACE_SPEC: CliOperationSpec = CliOperationSpec {
    name: "trace",
    family: "observability",
    summary: "Render one flow as a span waterfall.",
    description: "Fold the log into a span waterfall for one trace: spans nested by \
parent, offset by start, with attached events inline. Use --trace-id last for the most \
recent root trace.",
    args: &[
        SANDBOX_ID_ARG,
        ArgSpec::optional(
            "trace_id",
            ArgKind::String,
            "Trace id to render, or 'last' for the most recent root trace.",
            Some("last"),
            Some(ArgCliSpec {
                flag: Some("--trace-id"),
                positional: None,
            }),
        ),
    ],
    cli: Some(CliSpec {
        path: &["observability", "trace"],
        usage: "sandbox-cli observability trace --sandbox-id ID [--trace-id TRACE|last]",
        examples: &[
            "sandbox-cli observability trace --sandbox-id eos-abc --trace-id req-7f3",
            "sandbox-cli observability trace --sandbox-id eos-abc --trace-id last",
        ],
    }),
    related: &["events", "snapshot"],
};

const EVENTS_SPEC: CliOperationSpec = CliOperationSpec {
    name: "events",
    family: "observability",
    summary: "List domain-fact events across traces.",
    description: "Fold the log into a flat, cross-trace stream of point-in-time \
events (lease, errors, …), newest first. Filter by exact name and/or a \
start timestamp, and cap to the newest N with --last-n.",
    args: &[
        SANDBOX_ID_ARG,
        ArgSpec::optional(
            "name",
            ArgKind::String,
            "Filter to events with this exact name (e.g. lease.acquired).",
            None,
            Some(ArgCliSpec {
                flag: Some("--name"),
                positional: None,
            }),
        ),
        ArgSpec::optional(
            "since_ms",
            ArgKind::Integer,
            "Only events at or after this unix-ms timestamp.",
            None,
            Some(ArgCliSpec {
                flag: Some("--since-ms"),
                positional: None,
            }),
        ),
        ArgSpec::optional(
            "last_n",
            ArgKind::Integer,
            "Keep only the N newest matched events.",
            None,
            Some(ArgCliSpec {
                flag: Some("--last-n"),
                positional: None,
            }),
        ),
    ],
    cli: Some(CliSpec {
        path: &["observability", "events"],
        usage:
            "sandbox-cli observability events --sandbox-id ID [--name NAME] [--since-ms MS] [--last-n N]",
        examples: &[
            "sandbox-cli observability events --sandbox-id eos-abc",
            "sandbox-cli observability events --sandbox-id eos-abc --name lease.acquired",
            "sandbox-cli observability events --sandbox-id eos-abc --last-n 20",
        ],
    }),
    related: &["trace"],
};

const CGROUP_SPEC: CliOperationSpec = CliOperationSpec {
    name: "cgroup",
    family: "observability",
    summary: "Resource series for a scope (cpu/mem/io + disk).",
    description: "Fold the sample log for one scope into a time series with deltas: \
cgroup counters (cpu/mem/io from /sys/fs/cgroup) plus the disk sample (upperdir \
bytes/files) carried in the same record.",
    args: &[
        SANDBOX_ID_ARG,
        ArgSpec::optional(
            "scope",
            ArgKind::String,
            "Resource scope: 'sandbox' or a workspace id.",
            Some("sandbox"),
            Some(ArgCliSpec {
                flag: Some("--scope"),
                positional: None,
            }),
        ),
        ArgSpec::optional(
            "window_ms",
            ArgKind::Integer,
            "Lookback window in milliseconds (max 600000).",
            Some("60000"),
            Some(ArgCliSpec {
                flag: Some("--window-ms"),
                positional: None,
            }),
        ),
    ],
    cli: Some(CliSpec {
        path: &["observability", "cgroup"],
        usage: "sandbox-cli observability cgroup --sandbox-id ID [--scope SCOPE] [--window-ms MS]",
        examples: &[
            "sandbox-cli observability cgroup --sandbox-id eos-abc",
            "sandbox-cli observability cgroup --sandbox-id eos-abc --scope ws-1 --window-ms 60000",
        ],
    }),
    related: &["snapshot"],
};

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
            "workspace_id",
            ArgKind::String,
            "Show one workspace's lower layers and private upperdir.",
            None,
            Some(ArgCliSpec {
                flag: Some("--workspace-id"),
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
        usage:
            "sandbox-cli observability layerstack --sandbox-id ID [--workspace-id WS] [--window-ms MS]",
        examples: &[
            "sandbox-cli observability layerstack --sandbox-id eos-abc",
            "sandbox-cli observability layerstack --sandbox-id eos-abc --workspace-id ws-7",
        ],
    }),
    related: &["snapshot", "cgroup"],
};

const FAMILIES: &[&CliOperationFamilySpec] = &[&OBSERVABILITY_FAMILY];
const SPECS: &[&CliOperationSpec] = &[
    &SNAPSHOT_SPEC,
    &TRACE_SPEC,
    &EVENTS_SPEC,
    &CGROUP_SPEC,
    &LAYERSTACK_SPEC,
];

#[must_use]
pub fn observability_catalog() -> CliOperationCatalog {
    CliOperationCatalog::new(CliOperationExecutionSpace::Observability, FAMILIES, SPECS)
}
