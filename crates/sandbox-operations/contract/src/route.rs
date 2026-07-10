use crate::OperationScopeKind;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OperationScopePolicy {
    System,
    SandboxRequired,
    SystemOrSandbox,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OperationExecutionOwner {
    Manager,
    Runtime,
    Observability,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OperationVisibility {
    Public,
    Internal,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OperationRouteSpec {
    pub operation: &'static str,
    pub scope_policy: OperationScopePolicy,
    pub scope_kind: OperationScopeKind,
    pub execution_owner: OperationExecutionOwner,
    pub visibility: OperationVisibility,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OperationDispatchArgument {
    pub name: &'static str,
    pub value: &'static str,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OperationDispatchTarget {
    pub operation: &'static str,
    pub arguments: &'static [OperationDispatchArgument],
}
