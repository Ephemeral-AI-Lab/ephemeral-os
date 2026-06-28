use sandbox_protocol::{ArgCliSpec, ArgKind, ArgSpec, CliOperationSpec, CliSpec};

use super::SANDBOX_ID_ARG;

pub(super) const LAYERSTACK_SPEC: CliOperationSpec = CliOperationSpec {
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
