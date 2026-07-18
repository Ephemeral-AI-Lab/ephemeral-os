use sandbox_operation_contract::{OperationExecutionOwner, OperationSpec};

use super::SANDBOX_ID_ARG;
use crate::routed::{RoutedOperation, Routing};

pub const TOPOLOGY: RoutedOperation = RoutedOperation {
    spec: &TOPOLOGY_SPEC,
    routing: Routing::Sandbox(OperationExecutionOwner::Observability),
};

pub static TOPOLOGY_SPEC: OperationSpec = OperationSpec {
    name: "topology",
    family: "topology",
    summary: "Read one sandbox's workspace process topology.",
    description: "Explicitly contact one sandbox daemon and perform one bounded proc namespace topology collection. This operation does not return manager resource history.",
    args: &[SANDBOX_ID_ARG],
    related: &["resources", "cgroup"],
};
