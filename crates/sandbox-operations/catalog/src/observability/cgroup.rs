use sandbox_operation_contract::{ArgKind, ArgSpec, OperationExecutionOwner, OperationSpec};

use super::SANDBOX_ID_ARG;
use crate::routed::{RoutedOperation, Routing};

pub const CGROUP: RoutedOperation = RoutedOperation {
    spec: &CGROUP_SPEC,
    routing: Routing::Sandbox(OperationExecutionOwner::Observability),
};

pub static CGROUP_SPEC: OperationSpec = OperationSpec {
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
        ),
        ArgSpec::optional(
            "window_ms",
            ArgKind::Integer,
            "Lookback window in milliseconds (max 600000).",
            Some("60000"),
        ),
    ],
    related: &["snapshot"],
};
