use sandbox_operation_contract::{ArgKind, ArgSpec, OperationSpec};

use super::SANDBOX_ID_ARG;

pub(super) const EVENTS_SPEC: OperationSpec = OperationSpec {
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
        ),
        ArgSpec::optional(
            "since_ms",
            ArgKind::Integer,
            "Only events at or after this unix-ms timestamp.",
            None,
        ),
        ArgSpec::optional(
            "last_n",
            ArgKind::Integer,
            "Keep only the N newest matched events.",
            None,
        ),
    ],
    related: &["trace"],
};
