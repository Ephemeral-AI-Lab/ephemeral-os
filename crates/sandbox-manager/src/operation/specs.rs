use serde_json::{json, Value};

use sandbox_protocol::{
    ArgCliSpec, ArgKind, ArgSpec, CliSpec, OperationAuthority, OperationCatalog, OperationFamily,
    OperationSpec,
};

pub(crate) const CREATE_SANDBOX: OperationSpec = OperationSpec {
    name: "create_sandbox",
    family: OperationFamily::Run,
    summary: "Create a host-side sandbox record and runtime sandbox.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "sandboxes", "create"],
        usage: "create_sandbox {\"sandbox_id\":\"ID\"}",
        examples: &["create_sandbox {\"sandbox_id\":\"sbox-1\"}"],
    }),
};

pub(crate) const DESTROY_SANDBOX: OperationSpec = OperationSpec {
    name: "destroy_sandbox",
    family: OperationFamily::Run,
    summary: "Destroy a host-side sandbox and remove it from the registry.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "sandboxes", "destroy"],
        usage: "destroy_sandbox {\"sandbox_id\":\"ID\"}",
        examples: &["destroy_sandbox {\"sandbox_id\":\"sbox-1\"}"],
    }),
};

pub(crate) const LIST_SANDBOXES: OperationSpec = OperationSpec {
    name: "list_sandboxes",
    family: OperationFamily::Workspace,
    summary: "List sandbox records known to the manager.",
    args: &[],
    cli: Some(CliSpec {
        path: &["manager", "sandboxes", "list"],
        usage: "list_sandboxes {}",
        examples: &["list_sandboxes {}"],
    }),
};

pub(crate) const INSPECT_SANDBOX: OperationSpec = OperationSpec {
    name: "inspect_sandbox",
    family: OperationFamily::Workspace,
    summary: "Inspect one sandbox record.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "sandboxes", "inspect"],
        usage: "inspect_sandbox {\"sandbox_id\":\"ID\"}",
        examples: &["inspect_sandbox {\"sandbox_id\":\"sbox-1\"}"],
    }),
};

pub(crate) const START_SANDBOX_DAEMON: OperationSpec = OperationSpec {
    name: "start_sandbox_daemon",
    family: OperationFamily::Run,
    summary: "Install and start the selected sandbox daemon.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "sandboxes", "start-daemon"],
        usage: "start_sandbox_daemon {\"sandbox_id\":\"ID\"}",
        examples: &["start_sandbox_daemon {\"sandbox_id\":\"sbox-1\"}"],
    }),
};

pub(crate) const STOP_SANDBOX_DAEMON: OperationSpec = OperationSpec {
    name: "stop_sandbox_daemon",
    family: OperationFamily::Run,
    summary: "Stop the selected sandbox daemon and clear its endpoint.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "sandboxes", "stop-daemon"],
        usage: "stop_sandbox_daemon {\"sandbox_id\":\"ID\"}",
        examples: &["stop_sandbox_daemon {\"sandbox_id\":\"sbox-1\"}"],
    }),
};

pub(crate) const DESCRIBE_MANAGER_OPERATIONS: OperationSpec = OperationSpec {
    name: "describe_manager_operations",
    family: OperationFamily::Health,
    summary: "Describe manager operation specs.",
    args: &[],
    cli: Some(CliSpec {
        path: &["manager", "operations", "describe-manager"],
        usage: "describe_manager_operations {}",
        examples: &["describe_manager_operations {}"],
    }),
};

pub(crate) const DESCRIBE_DAEMON_OPERATIONS: OperationSpec = OperationSpec {
    name: "describe_daemon_operations",
    family: OperationFamily::Health,
    summary: "Describe operation specs from a selected sandbox daemon.",
    args: SANDBOX_ID_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "operations", "describe-daemon"],
        usage: "describe_daemon_operations {\"sandbox_id\":\"ID\"}",
        examples: &["describe_daemon_operations {\"sandbox_id\":\"sbox-1\"}"],
    }),
};

pub(crate) const INVOKE_SANDBOX_DAEMON: OperationSpec = OperationSpec {
    name: "invoke_sandbox_daemon",
    family: OperationFamily::Run,
    summary: "Forward a protocol request to a selected sandbox daemon.",
    args: INVOKE_SANDBOX_DAEMON_ARGS,
    cli: Some(CliSpec {
        path: &["manager", "operations", "invoke-daemon"],
        usage: "invoke_sandbox_daemon {\"sandbox_id\":\"ID\",\"request\":{...}}",
        examples: &[
            "invoke_sandbox_daemon {\"sandbox_id\":\"sbox-1\",\"request\":{\"op\":\"op\",\"request_id\":\"req-1\",\"args\":{}}}",
        ],
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

const INVOKE_SANDBOX_DAEMON_ARGS: &[ArgSpec] = &[
    ArgSpec::required(
        "sandbox_id",
        ArgKind::String,
        "Sandbox id.",
        Some(ArgCliSpec {
            flag: Some("--sandbox-id"),
            positional: None,
        }),
    ),
    ArgSpec::required(
        "request",
        ArgKind::String,
        "Nested protocol request object.",
        None,
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
    &INVOKE_SANDBOX_DAEMON,
];

#[must_use]
pub const fn operation_specs() -> &'static [&'static OperationSpec] {
    SPECS
}

#[must_use]
pub const fn operation_catalog() -> OperationCatalog {
    OperationCatalog::new(OperationAuthority::SandboxManager, operation_specs())
}

#[must_use]
pub(crate) fn catalog_value(catalog: OperationCatalog) -> Value {
    json!({
        "authority": authority_name(catalog.authority),
        "operations": catalog
            .operations
            .iter()
            .map(|spec| operation_spec_value(spec))
            .collect::<Vec<_>>(),
    })
}

fn operation_spec_value(spec: &OperationSpec) -> Value {
    json!({
        "name": spec.name,
        "family": family_name(spec.family),
        "summary": spec.summary,
        "args": spec.args.iter().map(arg_spec_value).collect::<Vec<_>>(),
    })
}

fn arg_spec_value(spec: &ArgSpec) -> Value {
    json!({
        "name": spec.name,
        "kind": arg_kind_name(spec.kind),
        "required": spec.required,
        "help": spec.help,
        "default": spec.default,
    })
}

fn authority_name(authority: OperationAuthority) -> &'static str {
    match authority {
        OperationAuthority::SandboxManager => "sandbox_manager",
        OperationAuthority::SandboxDaemon => "sandbox_daemon",
    }
}

fn family_name(family: OperationFamily) -> &'static str {
    match family {
        OperationFamily::Command => "command",
        OperationFamily::File => "file",
        OperationFamily::Workspace => "workspace",
        OperationFamily::Health => "health",
        OperationFamily::Run => "run",
    }
}

fn arg_kind_name(kind: ArgKind) -> &'static str {
    match kind {
        ArgKind::String => "string",
        ArgKind::Integer => "integer",
        ArgKind::Float => "float",
        ArgKind::Path => "path",
    }
}
