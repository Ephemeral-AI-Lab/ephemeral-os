use sandbox_protocol::{
    ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationCatalog, OperationExecutionSpace, OperationSpec,
};

pub(crate) const CREATE_SANDBOX: OperationSpec = OperationSpec {
    name: "create_sandbox",
    summary: "Create a host-side sandbox record and runtime sandbox.",
    args: CREATE_SANDBOX_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "create_sandbox"],
        usage: "sandbox-cli manager create_sandbox --image IMAGE --workspace-root PATH",
        examples: &[
            "sandbox-cli manager create_sandbox --image ubuntu:24.04 --workspace-root /testbed",
        ],
    }),
};

pub(crate) const DESTROY_SANDBOX: OperationSpec = OperationSpec {
    name: "destroy_sandbox",
    summary: "Destroy a host-side sandbox and remove it from the registry.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "destroy_sandbox"],
        usage: "sandbox-cli manager destroy_sandbox --sandbox-id ID",
        examples: &["sandbox-cli manager destroy_sandbox --sandbox-id sbox-1"],
    }),
};

pub(crate) const LIST_SANDBOXES: OperationSpec = OperationSpec {
    name: "list_sandboxes",
    summary: "List sandbox records known to the manager.",
    args: &[],
    cli: Some(CliSpec {
        path: &["manager", "list_sandboxes"],
        usage: "sandbox-cli manager list_sandboxes",
        examples: &["sandbox-cli manager list_sandboxes"],
    }),
};

pub(crate) const INSPECT_SANDBOX: OperationSpec = OperationSpec {
    name: "inspect_sandbox",
    summary: "Inspect one sandbox record.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "inspect_sandbox"],
        usage: "sandbox-cli manager inspect_sandbox --sandbox-id ID",
        examples: &["sandbox-cli manager inspect_sandbox --sandbox-id sbox-1"],
    }),
};

pub(crate) const START_SANDBOX_DAEMON: OperationSpec = OperationSpec {
    name: "start_sandbox_daemon",
    summary: "Install and start the selected sandbox daemon.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "start_sandbox_daemon"],
        usage: "sandbox-cli manager start_sandbox_daemon --sandbox-id ID",
        examples: &["sandbox-cli manager start_sandbox_daemon --sandbox-id sbox-1"],
    }),
};

pub(crate) const STOP_SANDBOX_DAEMON: OperationSpec = OperationSpec {
    name: "stop_sandbox_daemon",
    summary: "Stop the selected sandbox daemon and clear its endpoint.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "stop_sandbox_daemon"],
        usage: "sandbox-cli manager stop_sandbox_daemon --sandbox-id ID",
        examples: &["sandbox-cli manager stop_sandbox_daemon --sandbox-id sbox-1"],
    }),
};

pub(crate) const DESCRIBE_MANAGER_OPERATIONS: OperationSpec = OperationSpec {
    name: "describe_manager_operations",
    summary: "Describe manager operation specs.",
    args: &[],
    cli: Some(CliSpec {
        path: &["manager", "describe_manager_operations"],
        usage: "sandbox-cli manager describe_manager_operations",
        examples: &["sandbox-cli manager describe_manager_operations"],
    }),
};

pub(crate) const DESCRIBE_DAEMON_OPERATIONS: OperationSpec = OperationSpec {
    name: "describe_daemon_operations",
    summary: "Describe runtime operation specs for a selected sandbox.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "describe_daemon_operations"],
        usage: "sandbox-cli manager describe_daemon_operations --sandbox-id ID",
        examples: &["sandbox-cli manager describe_daemon_operations --sandbox-id sbox-1"],
    }),
};

const SANDBOX_ID_ARGS: &[ArgSpec] = &[ArgSpec::required(
    "sandbox_id",
    ArgKind::String,
    "Sandbox id.",
    Some(ArgCliSpec {
        flag: Some("--sandbox-id"),
        positional: None,
    }),
)];

const CREATE_SANDBOX_ARGS: &[ArgSpec] = &[
    ArgSpec::required(
        "image",
        ArgKind::String,
        "Container image used to create the sandbox.",
        Some(ArgCliSpec {
            flag: Some("--image"),
            positional: None,
        }),
    ),
    ArgSpec::required(
        "workspace_root",
        ArgKind::Path,
        "Absolute workspace root mounted inside this sandbox.",
        Some(ArgCliSpec {
            flag: Some("--workspace-root"),
            positional: None,
        }),
    ),
];

const SPECS: &[&OperationSpec] = &[
    &CREATE_SANDBOX,
    &DESTROY_SANDBOX,
    &LIST_SANDBOXES,
    &INSPECT_SANDBOX,
    &START_SANDBOX_DAEMON,
    &STOP_SANDBOX_DAEMON,
    &DESCRIBE_MANAGER_OPERATIONS,
    &DESCRIBE_DAEMON_OPERATIONS,
];

#[must_use]
pub const fn operation_specs() -> &'static [&'static OperationSpec] {
    SPECS
}

#[must_use]
pub const fn operation_catalog() -> OperationCatalog {
    OperationCatalog::new(OperationExecutionSpace::Manager, operation_specs())
}
