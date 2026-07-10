use sandbox_protocol::{ArgCliSpec, ArgKind, ArgSpec, CliOperationSpec, CliSpec};

use super::SANDBOX_ID_ARG;

pub(super) const EVENTS_SPEC: CliOperationSpec = CliOperationSpec {
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
            "sandbox-observability-cli events --sandbox-id ID [--name NAME] [--since-ms MS] [--last-n N]",
        examples: &[
            "sandbox-observability-cli events --sandbox-id eos-abc",
            "sandbox-observability-cli events --sandbox-id eos-abc --name lease.acquired",
            "sandbox-observability-cli events --sandbox-id eos-abc --last-n 20",
        ],
    }),
    related: &["trace"],
};
