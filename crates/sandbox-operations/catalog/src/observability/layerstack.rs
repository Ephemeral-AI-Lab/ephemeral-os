use sandbox_operation_contract::{ArgKind, ArgSpec, OperationSpec};

use super::SANDBOX_ID_ARG;

pub(super) const LAYERSTACK_SPEC: OperationSpec = OperationSpec {
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
        ),
        ArgSpec::optional(
            "window_ms",
            ArgKind::Integer,
            "Lookback window in milliseconds for the stack trend (max 600000).",
            Some("60000"),
        ),
    ],
    related: &["snapshot", "cgroup"],
};
