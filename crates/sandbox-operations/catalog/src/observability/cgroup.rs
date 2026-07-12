use sandbox_operation_contract::{ArgKind, ArgSpec, OperationExecutionOwner, OperationSpec};

use super::SANDBOX_ID_ARG;
use crate::routed::{RoutedOperation, Routing};

pub const CGROUP: RoutedOperation = RoutedOperation {
    spec: &CGROUP_SPEC,
    routing: Routing::Sandbox(OperationExecutionOwner::Manager),
};

pub static CGROUP_SPEC: OperationSpec = OperationSpec {
    name: "cgroup",
    family: "cgroup",
    summary: "Resource series for a scope (cpu/mem/io + disk).",
    description: "Return a read-only resource time series. Sandbox scope reads CPU, memory, \
and block-I/O counters from the host Docker Engine; workspace scopes retain daemon \
disk samples.",
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
