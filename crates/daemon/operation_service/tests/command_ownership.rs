pub mod support;

use std::path::PathBuf;
use std::sync::Arc;

use operation_service::command::{
    CancelCommandInput, CommandCallContext, CommandId, CommandServiceError, CommandStatus,
    ExecCommandInput, OperationTraceContext, PollCommandInput, ReadCommandLinesInput,
    WriteStdinInput,
};
use workspace::{CallerId, NetworkMode};

use support::{build_services, workspace_handle, FakeWorkspaceService, TestServices};

fn context(caller_id: &str) -> CommandCallContext {
    CommandCallContext {
        caller_id: CallerId(caller_id.to_owned()),
        trace: OperationTraceContext,
    }
}

fn exec_input(caller_id: &str, workspace_root: PathBuf) -> ExecCommandInput {
    ExecCommandInput {
        caller_id: CallerId(caller_id.to_owned()),
        workspace_root,
        workspace_id: None,
        cmd: "cat".to_owned(),
        cwd: None,
        timeout_seconds: None,
        yield_time_ms: Some(0),
    }
}

fn command_service_with_active_command() -> (TestServices, CommandId) {
    let fake = Arc::new(FakeWorkspaceService::new());
    let workspace_root = PathBuf::from("/workspace/one-shot");
    fake.push_create_result(Ok(workspace_handle(
        "workspace-one-shot",
        "caller-owner",
        "lease-1",
        workspace_root.clone(),
        NetworkMode::Host,
    )));
    let env = build_services(fake);
    let output = env
        .services
        .exec_command(
            exec_input("caller-owner", workspace_root),
            OperationTraceContext,
        )
        .expect("active command starts");
    let command_id = output.command_id.expect("running command id is returned");
    (env, command_id)
}

#[test]
fn command_ownership_rejects_wrong_caller_for_active_poll() {
    let (env, command_id) = command_service_with_active_command();

    let error = env
        .command
        .poll(
            PollCommandInput {
                command_id: command_id.clone(),
                last_n_lines: Some(10),
            },
            context("caller-other"),
        )
        .expect_err("wrong caller is rejected");

    assert!(matches!(
        error,
        CommandServiceError::CommandCallerMismatch { command_id: id, expected, actual }
            if id == command_id
                && expected == CallerId("caller-owner".to_owned())
                && actual == CallerId("caller-other".to_owned())
    ));
}

#[test]
fn command_ownership_validates_stdin_against_active_owner() {
    let (env, command_id) = command_service_with_active_command();

    let error = env
        .command
        .write_stdin(
            WriteStdinInput {
                command_id: command_id.clone(),
                chars: "hello\n".to_owned(),
                yield_time_ms: Some(0),
            },
            context("caller-other"),
        )
        .expect_err("wrong caller cannot write stdin");
    assert!(matches!(
        error,
        CommandServiceError::CommandCallerMismatch { command_id: id, expected, actual }
            if id == command_id
                && expected == CallerId("caller-owner".to_owned())
                && actual == CallerId("caller-other".to_owned())
    ));

    let output = env
        .command
        .write_stdin(
            WriteStdinInput {
                command_id,
                chars: "hello\n".to_owned(),
                yield_time_ms: Some(0),
            },
            context("caller-owner"),
        )
        .expect("owner can write stdin");
    assert_eq!(output.status, CommandStatus::Running);
}

#[test]
fn command_ownership_rejects_wrong_caller_for_active_read() {
    let (env, command_id) = command_service_with_active_command();

    let error = env
        .command
        .read_lines(
            ReadCommandLinesInput {
                command_id: command_id.clone(),
                offset: 0,
                limit: 1,
            },
            context("caller-other"),
        )
        .expect_err("wrong caller cannot read active command output");

    assert!(matches!(
        error,
        CommandServiceError::CommandCallerMismatch { command_id: id, expected, actual }
            if id == command_id
                && expected == CallerId("caller-owner".to_owned())
                && actual == CallerId("caller-other".to_owned())
    ));
}

#[test]
fn command_ownership_cancel_rejects_wrong_caller_and_marks_owner_request() {
    let (env, command_id) = command_service_with_active_command();

    let error = env
        .command
        .cancel(
            CancelCommandInput {
                command_id: command_id.clone(),
            },
            context("caller-other"),
        )
        .expect_err("wrong caller cannot cancel active command");
    assert!(matches!(
        error,
        CommandServiceError::CommandCallerMismatch { command_id: id, expected, actual }
            if id == command_id
                && expected == CallerId("caller-owner".to_owned())
                && actual == CallerId("caller-other".to_owned())
    ));

    let output = env
        .command
        .cancel(
            CancelCommandInput {
                command_id: command_id.clone(),
            },
            context("caller-owner"),
        )
        .expect("owner can cancel active command");

    assert_eq!(output.status, CommandStatus::Running);
}
