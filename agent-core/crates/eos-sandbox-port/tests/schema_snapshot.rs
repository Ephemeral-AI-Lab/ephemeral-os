// AC-sandbox-api-01: the schemars JSON schema for each request DTO is frozen by
// a crate-owned insta snapshot (the Rust source is a frozen `@DTO` with
// no `model_json_schema()`, so this snapshot — not a Rust golden — is the
// wire-shape contract; see the parity README). A change to a request DTO's
// field names / optionality / defaults fails the snapshot until reviewed.

use schemars::schema_for;

macro_rules! snapshot_schema {
    ($name:literal, $ty:ty) => {{
        let schema = serde_json::to_value(schema_for!($ty)).expect("schema serializes to json");
        insta::assert_json_snapshot!($name, schema);
    }};
}

#[test]
fn request_dto_schemas() {
    use eos_sandbox_port::{
        CommandSessionCancelRequest, EditFileRequest, EnterIsolatedWorkspaceRequest,
        ExecCommandRequest, ExecStdinRequest, ExitIsolatedWorkspaceRequest, ReadFileRequest,
        ReadCommandProgressRequest, ToolCallRequest, WriteFileRequest,
    };

    snapshot_schema!("read_file_request", ReadFileRequest);
    snapshot_schema!("write_file_request", WriteFileRequest);
    snapshot_schema!("edit_file_request", EditFileRequest);
    snapshot_schema!("exec_command_request", ExecCommandRequest);
    snapshot_schema!("exec_stdin_request", ExecStdinRequest);
    snapshot_schema!(
        "read_command_progress_request",
        ReadCommandProgressRequest
    );
    snapshot_schema!(
        "command_session_cancel_request",
        CommandSessionCancelRequest
    );
    snapshot_schema!(
        "enter_isolated_workspace_request",
        EnterIsolatedWorkspaceRequest
    );
    snapshot_schema!(
        "exit_isolated_workspace_request",
        ExitIsolatedWorkspaceRequest
    );
    snapshot_schema!("tool_call_request", ToolCallRequest);
}
