use sandbox_operation_contract::{OperationExecutionOwner, OperationSpec};

use super::SANDBOX_ID_ARG;
use crate::routed::{RoutedOperation, Routing};

pub const DAEMON: RoutedOperation = RoutedOperation {
    spec: &DAEMON_SPEC,
    routing: Routing::Sandbox(OperationExecutionOwner::Observability),
};

pub static DAEMON_SPEC: OperationSpec = OperationSpec {
    name: "daemon",
    family: "daemon",
    summary: "Read one sandbox daemon's bounded self-metrics.",
    description: "Explicitly contact one sandbox daemon and collect one bounded process, runtime, ownership, and diagnostic sample without enumerating workspace process topology.",
    args: &[SANDBOX_ID_ARG],
    related: &["resources", "topology", "cgroup"],
};
