use sandbox_protocol::{ArgCliSpec, ArgKind, ArgSpec, CliOperationSpec, CliSpec};

use super::SANDBOX_ID_ARG;

pub(super) const TRACE_SPEC: CliOperationSpec = CliOperationSpec {
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
        usage: "sandbox-manager-cli observability trace --sandbox-id ID [--trace-id TRACE|last]",
        examples: &[
            "sandbox-manager-cli observability trace --sandbox-id eos-abc --trace-id req-7f3",
            "sandbox-manager-cli observability trace --sandbox-id eos-abc --trace-id last",
        ],
    }),
    related: &["events", "snapshot"],
};
