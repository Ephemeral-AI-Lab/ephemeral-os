use sandbox_operation_contract::{
    ArgKind, ArgSpec, OperationExecutionOwner, OperationFamilySpec, OperationSpec,
};

use crate::routed::{RoutedOperation, Routing};

const RUNTIME_OWNED: Routing = Routing::Sandbox(OperationExecutionOwner::Runtime);

pub const CREATE_WORKSPACE_SESSION: RoutedOperation = RoutedOperation {
    spec: &CREATE_WORKSPACE_SESSION_SPEC,
    routing: RUNTIME_OWNED,
};

pub const DESTROY_WORKSPACE_SESSION: RoutedOperation = RoutedOperation {
    spec: &DESTROY_WORKSPACE_SESSION_SPEC,
    routing: RUNTIME_OWNED,
};

pub const WORKSPACE_SESSION_FAMILY: OperationFamilySpec = OperationFamilySpec {
    id: "workspace_session",
    title: "Workspace session",
    summary: "Create and destroy explicit workspace sessions.",
    description: "Create and destroy explicit workspace sessions that retain private changes until they are destroyed.",
};

pub const CREATE_WORKSPACE_SESSION_SPEC: OperationSpec = OperationSpec {
    name: "create_workspace_session",
    family: "workspace_session",
    summary: "Create an explicit workspace session.",
    description: "Create an explicit workspace session with finalize policy no_op. Commands and file operations can target the returned workspace_session_id. Private changes remain available while the session is live and are discarded when the session is destroyed.",
    args: CREATE_WORKSPACE_SESSION_ARGS,
    related: &["destroy_workspace_session", "exec_command"],
};

const CREATE_WORKSPACE_SESSION_ARGS: &[ArgSpec] = &[ArgSpec::optional(
    "network_profile",
    ArgKind::String,
    "Network profile for the session: shared or isolated. Defaults to shared.",
    Some("shared"),
)];

pub const DESTROY_WORKSPACE_SESSION_SPEC: OperationSpec = OperationSpec {
    name: "destroy_workspace_session",
    family: "workspace_session",
    summary: "Destroy an explicit workspace session.",
    description: "Destroy an explicit workspace session and discard its unpublished changes. The operation is rejected while the session has active commands.",
    args: DESTROY_WORKSPACE_SESSION_ARGS,
    related: &["create_workspace_session", "exec_command"],
};

const DESTROY_WORKSPACE_SESSION_ARGS: &[ArgSpec] = &[
    ArgSpec::required(
        "workspace_session_id",
        ArgKind::String,
        "Workspace session id to destroy.",
    ),
    ArgSpec::optional(
        "grace_s",
        ArgKind::Float,
        "Optional non-negative destroy grace period in seconds.",
        None,
    ),
];
