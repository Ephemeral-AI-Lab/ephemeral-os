use sandbox_operation_contract::{
    OperationDispatchArgument, OperationDispatchTarget, OperationExecutionOwner,
    OperationRouteSpec, OperationScopeKind, OperationScopePolicy, OperationVisibility,
};

pub const GET_OBSERVABILITY: &str = "get_observability";
pub const ROUTE: OperationRouteSpec = OperationRouteSpec {
    operation: GET_OBSERVABILITY,
    scope_policy: OperationScopePolicy::SandboxRequired,
    scope_kind: OperationScopeKind::Sandbox,
    execution_owner: OperationExecutionOwner::Observability,
    visibility: OperationVisibility::Internal,
};

#[must_use]
pub fn resolve(operation: &str, scope_kind: OperationScopeKind) -> Option<OperationDispatchTarget> {
    if scope_kind != OperationScopeKind::Sandbox {
        return None;
    }

    match operation {
        "snapshot" => Some(dispatch_target("snapshot")),
        "trace" => Some(dispatch_target("trace")),
        "events" => Some(dispatch_target("events")),
        "cgroup" => Some(dispatch_target("cgroup")),
        "layerstack" => Some(dispatch_target("layerstack")),
        _ => None,
    }
}

fn dispatch_target(view: &'static str) -> OperationDispatchTarget {
    OperationDispatchTarget {
        operation: GET_OBSERVABILITY,
        arguments: match view {
            "snapshot" => &[OperationDispatchArgument {
                name: "view",
                value: "snapshot",
            }],
            "trace" => &[OperationDispatchArgument {
                name: "view",
                value: "trace",
            }],
            "events" => &[OperationDispatchArgument {
                name: "view",
                value: "events",
            }],
            "cgroup" => &[OperationDispatchArgument {
                name: "view",
                value: "cgroup",
            }],
            "layerstack" => &[OperationDispatchArgument {
                name: "view",
                value: "layerstack",
            }],
            _ => &[],
        },
    }
}
