use sandbox_operation_contract::{ArgKind, ArgSpec, OperationSpec};

use super::SANDBOX_ID_ARG;

pub(super) const TRACE_SPEC: OperationSpec = OperationSpec {
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
        ),
    ],
    related: &["events", "snapshot"],
};
