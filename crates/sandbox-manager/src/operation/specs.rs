use sandbox_protocol::{
    ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationCatalog, OperationExecutionSpace,
    OperationFamily, OperationSpec,
};

pub(crate) const CREATE_SANDBOX: OperationSpec = OperationSpec {
    name: "create_sandbox",
    family: OperationFamily::Run,
    summary: "Create a host-side sandbox record and runtime sandbox.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "create_sandbox"],
        usage: "sandbox manager create_sandbox --sandbox-id ID",
        examples: &["sandbox manager create_sandbox --sandbox-id sbox-1"],
    }),
};

pub(crate) const DESTROY_SANDBOX: OperationSpec = OperationSpec {
    name: "destroy_sandbox",
    family: OperationFamily::Run,
    summary: "Destroy a host-side sandbox and remove it from the registry.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "destroy_sandbox"],
        usage: "sandbox manager destroy_sandbox --sandbox-id ID",
        examples: &["sandbox manager destroy_sandbox --sandbox-id sbox-1"],
    }),
};

pub(crate) const LIST_SANDBOXES: OperationSpec = OperationSpec {
    name: "list_sandboxes",
    family: OperationFamily::Workspace,
    summary: "List sandbox records known to the manager.",
    args: &[],
    cli: Some(CliSpec {
        path: &["manager", "list_sandboxes"],
        usage: "sandbox manager list_sandboxes",
        examples: &["sandbox manager list_sandboxes"],
    }),
};

pub(crate) const INSPECT_SANDBOX: OperationSpec = OperationSpec {
    name: "inspect_sandbox",
    family: OperationFamily::Workspace,
    summary: "Inspect one sandbox record.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "inspect_sandbox"],
        usage: "sandbox manager inspect_sandbox --sandbox-id ID",
        examples: &["sandbox manager inspect_sandbox --sandbox-id sbox-1"],
    }),
};

pub(crate) const START_SANDBOX_DAEMON: OperationSpec = OperationSpec {
    name: "start_sandbox_daemon",
    family: OperationFamily::Run,
    summary: "Install and start the selected sandbox daemon.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "start_sandbox_daemon"],
        usage: "sandbox manager start_sandbox_daemon --sandbox-id ID",
        examples: &["sandbox manager start_sandbox_daemon --sandbox-id sbox-1"],
    }),
};

pub(crate) const STOP_SANDBOX_DAEMON: OperationSpec = OperationSpec {
    name: "stop_sandbox_daemon",
    family: OperationFamily::Run,
    summary: "Stop the selected sandbox daemon and clear its endpoint.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "stop_sandbox_daemon"],
        usage: "sandbox manager stop_sandbox_daemon --sandbox-id ID",
        examples: &["sandbox manager stop_sandbox_daemon --sandbox-id sbox-1"],
    }),
};

pub(crate) const DESCRIBE_MANAGER_OPERATIONS: OperationSpec = OperationSpec {
    name: "describe_manager_operations",
    family: OperationFamily::Health,
    summary: "Describe manager operation specs.",
    args: &[],
    cli: Some(CliSpec {
        path: &["manager", "describe_manager_operations"],
        usage: "sandbox manager describe_manager_operations",
        examples: &["sandbox manager describe_manager_operations"],
    }),
};

pub(crate) const DESCRIBE_DAEMON_OPERATIONS: OperationSpec = OperationSpec {
    name: "describe_daemon_operations",
    family: OperationFamily::Health,
    summary: "Describe runtime operation specs for a selected sandbox.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "describe_daemon_operations"],
        usage: "sandbox manager describe_daemon_operations --sandbox-id ID",
        examples: &["sandbox manager describe_daemon_operations --sandbox-id sbox-1"],
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
