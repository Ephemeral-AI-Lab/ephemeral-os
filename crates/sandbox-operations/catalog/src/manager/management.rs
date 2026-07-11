//! Management operation declarations.

use sandbox_operation_contract::{
    ArgKind, ArgSpec, OperationExecutionOwner, OperationFamilySpec, OperationSpec,
};

use crate::routed::{RoutedOperation, Routing};

const MANAGER_OWNED: Routing = Routing::System(OperationExecutionOwner::Manager);

pub const MANAGEMENT_FAMILY: OperationFamilySpec = OperationFamilySpec {
    id: "management",
    title: "Management",
    summary: "Manage sandbox records, compact layer stacks, and export published changes.",
    description: "Create, destroy, list, and inspect sandbox records; compact published layer stacks; and export published changes. Daemons are managed as part of sandbox lifecycle behavior, not as standalone manager operations.",
};

pub const CREATE_SANDBOX_SPEC: OperationSpec = OperationSpec {
    name: "create_sandbox",
    family: "management",
    summary: "Create a host-side sandbox record and runtime sandbox.",
    description:
        "Create a host-side sandbox record, create the runtime sandbox, and start its daemon.",
    args: CREATE_SANDBOX_ARGS,
    related: &["list_sandboxes", "inspect_sandbox", "destroy_sandbox"],
};

const CREATE_SANDBOX_ARGS: &[ArgSpec] = &[
    ArgSpec::required(
        "image",
        ArgKind::String,
        "Container image used to create the sandbox.",
    ),
    ArgSpec::required(
        "workspace_root",
        ArgKind::Path,
        "Absolute host workspace directory bind-mounted into this sandbox.",
    ),
    ArgSpec::optional(
        "count",
        ArgKind::Integer,
        "Number of sandboxes to create (minimum 1). Values greater than 1 use a shared read-only workspace base.",
        Some("1"),
    ),
];

pub const LIST_DOCKER_IMAGES_SPEC: OperationSpec = OperationSpec {
    name: "list_docker_images",
    family: "management",
    summary: "List local Docker image references available for sandbox creation.",
    description:
        "List every local Docker image reference, including untagged image IDs that can create a sandbox.",
    args: &[],
    related: &["create_sandbox"],
};

pub const LIST_WORKSPACE_DIRECTORIES_SPEC: OperationSpec = OperationSpec {
    name: "list_workspace_directories",
    family: "management",
    summary: "List local workspace directories available for sandbox creation.",
    description:
        "List picker-visible workspace roots when path is omitted, or up to 500 immediate local subdirectories of a selected workspace directory.",
    args: LIST_WORKSPACE_DIRECTORIES_ARGS,
    related: &["create_sandbox"],
};

const LIST_WORKSPACE_DIRECTORIES_ARGS: &[ArgSpec] = &[ArgSpec::optional(
    "path",
    ArgKind::Path,
    "Absolute picker-visible workspace directory to browse. Omit to list roots.",
    None,
)];

pub const DESTROY_SANDBOX_SPEC: OperationSpec = OperationSpec {
    name: "destroy_sandbox",
    family: "management",
    summary: "Destroy a host-side sandbox and remove it from the registry.",
    description: "Stop the sandbox daemon, destroy the runtime sandbox, and remove the host-side sandbox record.",
    args: DESTROY_SANDBOX_ARGS,
    related: &["list_sandboxes", "inspect_sandbox"],
};

const DESTROY_SANDBOX_ARGS: &[ArgSpec] = &[ArgSpec::required(
    "sandbox_id",
    ArgKind::String,
    "Sandbox id.",
)];

pub const LIST_SANDBOXES_SPEC: OperationSpec = OperationSpec {
    name: "list_sandboxes",
    family: "management",
    summary: "List sandbox records known to the manager.",
    description: "List sandbox records known to the manager, including lifecycle state and configured daemon endpoint metadata.",
    args: &[],
    related: &["inspect_sandbox", "create_sandbox"],
};

pub const INSPECT_SANDBOX_SPEC: OperationSpec = OperationSpec {
    name: "inspect_sandbox",
    family: "management",
    summary: "Inspect one sandbox record.",
    description: "Inspect one sandbox record, including lifecycle state, workspace root, and configured daemon endpoint metadata.",
    args: INSPECT_SANDBOX_ARGS,
    related: &["list_sandboxes"],
};

const INSPECT_SANDBOX_ARGS: &[ArgSpec] = &[ArgSpec::required(
    "sandbox_id",
    ArgKind::String,
    "Sandbox id.",
)];

pub const SQUASH_LAYERSTACKS_SPEC: OperationSpec = OperationSpec {
    name: "squash_layerstacks",
    family: "management",
    summary: "Squash a sandbox's layer stack and live-remount its sessions.",
    description: "Squash every squashable block of the selected sandbox's published layers into equivalent flattened layers and migrate live workspace sessions onto the compact chains. Forwards one squash_layerstack request to the sandbox daemon.",
    args: SQUASH_LAYERSTACKS_ARGS,
    related: &["list_sandboxes", "inspect_sandbox", "export_changes"],
};

const SQUASH_LAYERSTACKS_ARGS: &[ArgSpec] = &[ArgSpec::required(
    "sandbox_id",
    ArgKind::String,
    "Sandbox id.",
)];

pub const EXPORT_CHANGES_SPEC: OperationSpec = OperationSpec {
    name: "export_changes",
    family: "management",
    summary: "Export a sandbox's published changes to a host path.",
    description: "Fold every published layer above the base (newest-wins, \
                  whiteout/opaque aware) into a compressed delta stream, \
                  fetch it from the sandbox daemon, and apply it onto \
                  --dest or write it as an archive. Forwards \
                  export_layerstack and read_export_chunk requests to the \
                  sandbox daemon.",
    args: EXPORT_CHANGES_ARGS,
    related: &["inspect_sandbox", "squash_layerstacks"],
};

const EXPORT_CHANGES_ARGS: &[ArgSpec] = &[
    ArgSpec::required("sandbox_id", ArgKind::String, "Sandbox id."),
    ArgSpec::required(
        "dest",
        ArgKind::Path,
        "Absolute host destination: directory for dir format, archive file for tar formats.",
    ),
    ArgSpec::optional(
        "format",
        ArgKind::String,
        "Output format: dir, tar, or tar-zst.",
        Some("dir"),
    ),
];

pub const CREATE_SANDBOX: RoutedOperation = RoutedOperation {
    spec: &CREATE_SANDBOX_SPEC,
    routing: MANAGER_OWNED,
};

pub const LIST_DOCKER_IMAGES: RoutedOperation = RoutedOperation {
    spec: &LIST_DOCKER_IMAGES_SPEC,
    routing: MANAGER_OWNED,
};

pub const LIST_WORKSPACE_DIRECTORIES: RoutedOperation = RoutedOperation {
    spec: &LIST_WORKSPACE_DIRECTORIES_SPEC,
    routing: MANAGER_OWNED,
};

pub const DESTROY_SANDBOX: RoutedOperation = RoutedOperation {
    spec: &DESTROY_SANDBOX_SPEC,
    routing: MANAGER_OWNED,
};

pub const LIST_SANDBOXES: RoutedOperation = RoutedOperation {
    spec: &LIST_SANDBOXES_SPEC,
    routing: MANAGER_OWNED,
};

pub const INSPECT_SANDBOX: RoutedOperation = RoutedOperation {
    spec: &INSPECT_SANDBOX_SPEC,
    routing: MANAGER_OWNED,
};

pub const SQUASH_LAYERSTACKS: RoutedOperation = RoutedOperation {
    spec: &SQUASH_LAYERSTACKS_SPEC,
    routing: MANAGER_OWNED,
};

pub const EXPORT_CHANGES: RoutedOperation = RoutedOperation {
    spec: &EXPORT_CHANGES_SPEC,
    routing: MANAGER_OWNED,
};
