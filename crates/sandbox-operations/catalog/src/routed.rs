//! Single-declaration operation authoring: one semantic spec plus its public
//! routing, expanded into route rows at compile time.

use sandbox_operation_contract::{
    OperationExecutionOwner, OperationRouteSpec, OperationScopeKind, OperationScopePolicy,
    OperationSpec, OperationVisibility,
};

/// One public operation declared once: the semantic spec and how it routes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RoutedOperation {
    pub spec: &'static OperationSpec,
    pub routing: Routing,
}

/// Public scope expansion for one operation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Routing {
    System(OperationExecutionOwner),
    Sandbox(OperationExecutionOwner),
    SystemOrSandbox {
        system: OperationExecutionOwner,
        sandbox: OperationExecutionOwner,
    },
}

/// Number of public route rows `operations` expands to.
#[must_use]
pub const fn route_count(operations: &[&RoutedOperation]) -> usize {
    let mut count = 0;
    let mut index = 0;
    while index < operations.len() {
        count += match operations[index].routing {
            Routing::SystemOrSandbox { .. } => 2,
            Routing::System(_) | Routing::Sandbox(_) => 1,
        };
        index += 1;
    }
    count
}

/// Expand `operations` into their public route rows, in declaration order.
#[must_use]
pub const fn expand_routes<const COUNT: usize>(
    operations: &'static [&'static RoutedOperation],
) -> [OperationRouteSpec; COUNT] {
    let mut routes = [PLACEHOLDER_ROUTE; COUNT];
    let mut row = 0;
    let mut index = 0;
    while index < operations.len() {
        let operation = operations[index];
        let name = operation.spec.name;
        match operation.routing {
            Routing::System(owner) => {
                routes[row] = public_route(
                    name,
                    OperationScopePolicy::System,
                    OperationScopeKind::System,
                    owner,
                );
                row += 1;
            }
            Routing::Sandbox(owner) => {
                routes[row] = public_route(
                    name,
                    OperationScopePolicy::SandboxRequired,
                    OperationScopeKind::Sandbox,
                    owner,
                );
                row += 1;
            }
            Routing::SystemOrSandbox { system, sandbox } => {
                routes[row] = public_route(
                    name,
                    OperationScopePolicy::SystemOrSandbox,
                    OperationScopeKind::System,
                    system,
                );
                row += 1;
                routes[row] = public_route(
                    name,
                    OperationScopePolicy::SystemOrSandbox,
                    OperationScopeKind::Sandbox,
                    sandbox,
                );
                row += 1;
            }
        }
        index += 1;
    }
    routes
}

/// Collect each declared operation's spec, in declaration order.
#[must_use]
pub const fn specs<const COUNT: usize>(
    operations: &'static [&'static RoutedOperation],
) -> [&'static OperationSpec; COUNT] {
    let mut collected = [operations[0].spec; COUNT];
    let mut index = 0;
    while index < operations.len() {
        collected[index] = operations[index].spec;
        index += 1;
    }
    collected
}

const PLACEHOLDER_ROUTE: OperationRouteSpec = OperationRouteSpec {
    operation: "",
    scope_policy: OperationScopePolicy::System,
    scope_kind: OperationScopeKind::System,
    execution_owner: OperationExecutionOwner::Manager,
    visibility: OperationVisibility::Public,
};

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
