use sandbox_operation_contract::{
    OperationExecutionOwner, OperationRouteSpec, OperationScopeKind, OperationScopePolicy,
    OperationVisibility,
};

#[cfg(feature = "manager")]
const MANAGER_ROUTES: &[OperationRouteSpec] = &[
    public_route(
        "create_sandbox",
        OperationScopePolicy::System,
        OperationScopeKind::System,
        OperationExecutionOwner::Manager,
    ),
    public_route(
        "destroy_sandbox",
        OperationScopePolicy::System,
        OperationScopeKind::System,
        OperationExecutionOwner::Manager,
    ),
    public_route(
        "list_sandboxes",
        OperationScopePolicy::System,
        OperationScopeKind::System,
        OperationExecutionOwner::Manager,
    ),
    public_route(
        "inspect_sandbox",
        OperationScopePolicy::System,
        OperationScopeKind::System,
        OperationExecutionOwner::Manager,
    ),
    public_route(
        "squash_layerstacks",
        OperationScopePolicy::System,
        OperationScopeKind::System,
        OperationExecutionOwner::Manager,
    ),
    public_route(
        "export_changes",
        OperationScopePolicy::System,
        OperationScopeKind::System,
        OperationExecutionOwner::Manager,
    ),
];

#[cfg(feature = "runtime")]
const RUNTIME_ROUTES: &[OperationRouteSpec] = &[
    runtime_public_route("exec_command"),
    runtime_public_route("write_command_stdin"),
    runtime_public_route("read_command_lines"),
    runtime_public_route("file_read"),
    runtime_public_route("file_write"),
    runtime_public_route("file_edit"),
    runtime_public_route("file_blame"),
];

#[cfg(feature = "observability")]
const OBSERVABILITY_ROUTES: &[OperationRouteSpec] = &[
    public_route(
        "snapshot",
        OperationScopePolicy::SystemOrSandbox,
        OperationScopeKind::System,
        OperationExecutionOwner::Manager,
    ),
    public_route(
        "snapshot",
        OperationScopePolicy::SystemOrSandbox,
        OperationScopeKind::Sandbox,
        OperationExecutionOwner::Observability,
    ),
    observability_public_route("trace"),
    observability_public_route("events"),
    observability_public_route("cgroup"),
    observability_public_route("layerstack"),
];

const fn public_route(
    operation: &'static str,
    scope_policy: OperationScopePolicy,
    scope_kind: OperationScopeKind,
    execution_owner: OperationExecutionOwner,
) -> OperationRouteSpec {
    OperationRouteSpec {
        operation,
        scope_policy,
        scope_kind,
        execution_owner,
        visibility: OperationVisibility::Public,
    }
}

#[cfg(feature = "runtime")]
const fn runtime_public_route(operation: &'static str) -> OperationRouteSpec {
    public_route(
        operation,
        OperationScopePolicy::SandboxRequired,
        OperationScopeKind::Sandbox,
        OperationExecutionOwner::Runtime,
    )
}

#[cfg(feature = "observability")]
const fn observability_public_route(operation: &'static str) -> OperationRouteSpec {
    public_route(
        operation,
        OperationScopePolicy::SandboxRequired,
        OperationScopeKind::Sandbox,
        OperationExecutionOwner::Observability,
    )
}

#[cfg(feature = "manager")]
#[must_use]
pub const fn manager_routes() -> &'static [OperationRouteSpec] {
    MANAGER_ROUTES
}

#[cfg(feature = "runtime")]
#[must_use]
pub const fn runtime_routes() -> &'static [OperationRouteSpec] {
    RUNTIME_ROUTES
}

#[cfg(feature = "observability")]
#[must_use]
pub const fn observability_routes() -> &'static [OperationRouteSpec] {
    OBSERVABILITY_ROUTES
}

#[cfg(all(feature = "manager", feature = "runtime", feature = "observability"))]
const PUBLIC_ROUTE_SETS: &[&[OperationRouteSpec]] =
    &[MANAGER_ROUTES, RUNTIME_ROUTES, OBSERVABILITY_ROUTES];

#[cfg(all(feature = "manager", feature = "runtime", feature = "observability"))]
pub fn public_routes() -> impl Iterator<Item = &'static OperationRouteSpec> {
    PUBLIC_ROUTE_SETS.iter().copied().flatten()
}
