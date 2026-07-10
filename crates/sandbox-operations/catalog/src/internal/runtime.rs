use sandbox_operation_contract::{
    OperationExecutionOwner, OperationRouteSpec, OperationScopeKind, OperationScopePolicy,
    OperationVisibility,
};

pub const CREATE_WORKSPACE_SESSION: &str = "create_workspace_session";
pub const DESTROY_WORKSPACE_SESSION: &str = "destroy_workspace_session";
pub const SQUASH_LAYERSTACK: &str = "squash_layerstack";
pub const EXPORT_LAYERSTACK: &str = "export_layerstack";
pub const READ_EXPORT_CHUNK: &str = "read_export_chunk";
pub const FILE_LIST: &str = "file_list";

pub const ROUTES: &[OperationRouteSpec] = &[
    internal_runtime_route(CREATE_WORKSPACE_SESSION),
    internal_runtime_route(DESTROY_WORKSPACE_SESSION),
    internal_runtime_route(SQUASH_LAYERSTACK),
    internal_runtime_route(EXPORT_LAYERSTACK),
    internal_runtime_route(READ_EXPORT_CHUNK),
];

const fn internal_runtime_route(operation: &'static str) -> OperationRouteSpec {
    OperationRouteSpec {
        operation,
        scope_policy: OperationScopePolicy::SandboxRequired,
        scope_kind: OperationScopeKind::Sandbox,
        execution_owner: OperationExecutionOwner::Runtime,
        visibility: OperationVisibility::Internal,
    }
}
