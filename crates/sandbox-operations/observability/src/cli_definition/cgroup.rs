use sandbox_protocol::{ArgCliSpec, ArgKind, ArgSpec, CliOperationSpec, CliSpec};

use super::SANDBOX_ID_ARG;

pub(super) const CGROUP_SPEC: CliOperationSpec = CliOperationSpec {
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
        usage: "sandbox-observability-cli cgroup --sandbox-id ID [--scope SCOPE] [--window-ms MS]",
        examples: &[
            "sandbox-observability-cli cgroup --sandbox-id eos-abc",
            "sandbox-observability-cli cgroup --sandbox-id eos-abc --scope ws-1 --window-ms 60000",
        ],
    }),
    related: &["snapshot"],
};
