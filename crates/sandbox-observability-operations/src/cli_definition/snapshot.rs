use sandbox_protocol::{ArgCliSpec, ArgKind, ArgSpec, CliOperationSpec, CliSpec};

const SNAPSHOT_SANDBOX_ID_ARG: ArgSpec = ArgSpec::optional(
    "sandbox_id",
    ArgKind::String,
    "Optional target sandbox id. When omitted, the manager queries all ready sandboxes.",
    None,
    Some(ArgCliSpec {
        flag: Some("--sandbox-id"),
        positional: None,
    }),
);

pub static SNAPSHOT_SPEC: CliOperationSpec = CliOperationSpec {
    name: "snapshot",
    family: "observability",
    summary: "Show live sandbox state.",
    description: "Show current state from the runtime registry for one sandbox, or \
aggregate ready manager-known sandboxes when --sandbox-id is omitted: sandbox lifecycle \
state, workspaces (with layer counts), in-flight executions, and the latest \
resource sample per scope. Served live; does not read the log.",
    args: &[SNAPSHOT_SANDBOX_ID_ARG],
    cli: Some(CliSpec {
        path: &["observability", "snapshot"],
        usage: "sandbox-observability-cli snapshot [--sandbox-id ID]",
        examples: &[
            "sandbox-observability-cli snapshot",
            "sandbox-observability-cli snapshot --sandbox-id eos-abc",
        ],
    }),
    related: &["trace", "cgroup"],
};
