use sandbox_operation_contract::{ArgKind, ArgSpec, OperationExecutionOwner, OperationSpec};

use super::SANDBOX_ID_ARG;
use crate::routed::{RoutedOperation, Routing};

pub const TRACE: RoutedOperation = RoutedOperation {
    spec: &TRACE_SPEC,
    routing: Routing::Sandbox(OperationExecutionOwner::Observability),
};

pub static TRACE_SPEC: OperationSpec = OperationSpec {
    name: "trace",
    family: "trace",
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
        ),
    ],
    related: &["events", "snapshot"],
};
