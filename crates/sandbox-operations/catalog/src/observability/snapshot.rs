use sandbox_operation_contract::{ArgKind, ArgSpec, OperationExecutionOwner, OperationSpec};

use crate::routed::{RoutedOperation, Routing};

pub const SNAPSHOT: RoutedOperation = RoutedOperation {
    spec: &SNAPSHOT_SPEC,
    routing: Routing::SystemOrSandbox {
        system: OperationExecutionOwner::Manager,
        sandbox: OperationExecutionOwner::Observability,
    },
};

const SNAPSHOT_SANDBOX_ID_ARG: ArgSpec = ArgSpec::optional(
    "sandbox_id",
    ArgKind::String,
    "Optional target sandbox id. When omitted, the manager queries all ready sandboxes.",
    None,
);

pub static SNAPSHOT_SPEC: OperationSpec = OperationSpec {
    name: "snapshot",
    family: "snapshot",
    summary: "Show live sandbox state.",
    description: "Show current state from the runtime registry for one sandbox, or \
aggregate ready manager-known sandboxes when --sandbox-id is omitted: sandbox lifecycle \
state, workspaces (with layer counts), in-flight executions, and the latest \
resource sample per scope. Served live; does not read the log.",
    args: &[SNAPSHOT_SANDBOX_ID_ARG],
    related: &["trace", "cgroup"],
};
