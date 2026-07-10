use sandbox_operation_contract::{ArgKind, ArgSpec, OperationFamilySpec, OperationSpec};

pub const COMMAND_FAMILY: OperationFamilySpec = OperationFamilySpec {
    id: "command",
    title: "Command",
    summary: "Run, interact with, and inspect commands.",
    description: "Run, interact with, and inspect commands inside the active sandbox runtime.",
};

pub const EXEC_COMMAND_SPEC: OperationSpec = OperationSpec {
    name: "exec_command",
    family: "command",
    summary: "Start a command in a workspace session.",
    description: "Start a shell command in a workspace session. With workspace_session_id, run inside that existing session. Without it, exec_command creates an automatic session with finalize policy publish_then_destroy: after its last command reaches terminal state, the runtime captures and publishes the session's changes to the layerstack, then destroys the session. Explicitly managed sessions remain alive until internal teardown and discard unpublished changes when torn down. File operations and remounts run under the session's admission gate and neither extend nor trigger the session lifecycle. If the command is still running after the initial wait, the response includes a command_session_id usable with read_command_lines or write_command_stdin; a still-running command stays terminable through write_command_stdin (Ctrl-C or Ctrl-D).",
    args: EXEC_COMMAND_ARGS,
    related: &["write_command_stdin", "read_command_lines"],
};

const EXEC_COMMAND_ARGS: &[ArgSpec] = &[
    ArgSpec::optional(
        "workspace_session_id",
        ArgKind::String,
        "Existing workspace session id to run inside. Omit to create a session with finalize policy publish_then_destroy.",
        None,
    ),
    ArgSpec::required("cmd", ArgKind::String, "Shell command text."),
    ArgSpec::optional(
        "timeout_ms",
        ArgKind::Integer,
        "Command timeout in milliseconds.",
        None,
    ),
    ArgSpec::optional(
        "yield_time_ms",
        ArgKind::Integer,
        "Initial output wait in milliseconds.",
        None,
    ),
];

pub const WRITE_STDIN_SPEC: OperationSpec = OperationSpec {
    name: "write_command_stdin",
    family: "command",
    summary: "Write text to a running command stdin.",
    description: "Append text to the stdin stream of a running command session and return a bounded output yield.",
    args: WRITE_STDIN_ARGS,
    related: &["exec_command", "read_command_lines"],
};

const WRITE_STDIN_ARGS: &[ArgSpec] = &[
    ArgSpec::required(
        "command_session_id",
        ArgKind::String,
        "Command session id returned by exec_command.",
    ),
    ArgSpec::required("stdin", ArgKind::String, "Text to write to stdin."),
    ArgSpec::optional(
        "yield_time_ms",
        ArgKind::Integer,
        "Output wait after writing stdin.",
        None,
    ),
];

pub const READ_LINES_SPEC: OperationSpec = OperationSpec {
    name: "read_command_lines",
    family: "command",
    summary: "Read command output by line offset.",
    description: "Read rendered command output for a command session using stable line offsets.",
    args: READ_LINES_ARGS,
    related: &["exec_command", "write_command_stdin"],
};

const READ_LINES_ARGS: &[ArgSpec] = &[
    ArgSpec::required(
        "command_session_id",
        ArgKind::String,
        "Command session id returned by exec_command.",
    ),
    ArgSpec::optional(
        "start_offset",
        ArgKind::Integer,
        "First transcript line offset. Defaults to 0.",
        Some("0"),
    ),
    ArgSpec::optional(
        "limit",
        ArgKind::Integer,
        "Maximum transcript rows to return. Defaults to 200; maximum 1000.",
        Some("200"),
    ),
];
