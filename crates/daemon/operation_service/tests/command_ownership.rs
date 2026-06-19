pub mod support;

use std::path::PathBuf;
use std::sync::Arc;

use command::yield_wait_loop::WaitOutcome;
use operation_service::command::{
    CancelCommandInput, CommandCallContext, CommandId, CommandServiceError, CommandStatus,
    ExecCommandInput, OperationTraceContext, PollCommandInput, ReadCommandLinesInput,
    WriteStdinInput,
};
use workspace::{CallerId, WorkspaceProfile};

use support::{
    build_services, build_services_with_launch_driver, create_request, success_exit,
    workspace_handle, FakeLaunchDriver, FakeWorkspaceService, TestServices,
};

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
        workspace_session_id: None,
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
        WorkspaceProfile::HostCompatible,
    )));
    let env = build_services(fake);
    let output = env
        .command
        .exec_command(
            exec_input("caller-owner", workspace_root),
            context("caller-owner"),
        )
        .expect("active command starts");
    let command_id = output.command_id.expect("running command id is returned");
    (env, command_id)
}

fn command_service_with_completed_command() -> (TestServices, CommandId) {
    let fake = Arc::new(FakeWorkspaceService::new());
    let workspace_root = PathBuf::from("/workspace/session");
    fake.push_create_result(Ok(workspace_handle(
        "workspace-session",
        "caller-owner",
        "lease-1",
        workspace_root.clone(),
        WorkspaceProfile::HostCompatible,
    )));
    let driver = Arc::new(FakeLaunchDriver::new());
    driver.push_outcome(WaitOutcome::Completed(success_exit("done\n")));
    let env = build_services_with_launch_driver(fake, driver);
    let handler = env
        .workspace
        .create_workspace_session(create_request("caller-owner", workspace_root.clone()))
        .expect("session create succeeds");
    let output = env
        .command
        .exec_command(
            ExecCommandInput {
                workspace_session_id: Some(handler.workspace_session_id),
                ..exec_input("caller-owner", workspace_root)
            },
            context("caller-owner"),
        )
        .expect("command completes");
    let command_id = output.command_id.expect("completed command id is returned");
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

#[test]
fn command_ownership_rejects_wrong_caller_for_completed_poll_stdin_and_cancel() {
    let (env, command_id) = command_service_with_completed_command();

    let poll_error = env
        .command
        .poll(
            PollCommandInput {
                command_id: command_id.clone(),
                last_n_lines: Some(1),
            },
            context("caller-other"),
        )
        .expect_err("wrong caller cannot poll completed command");
    let stdin_error = env
        .command
        .write_stdin(
            WriteStdinInput {
                command_id: command_id.clone(),
                chars: "ignored".to_owned(),
                yield_time_ms: Some(0),
            },
            context("caller-other"),
        )
        .expect_err("wrong caller cannot write stdin to completed command");
    let cancel_error = env
        .command
        .cancel(
            CancelCommandInput {
                command_id: command_id.clone(),
            },
            context("caller-other"),
        )
        .expect_err("wrong caller cannot cancel completed command");

    for error in [poll_error, stdin_error, cancel_error] {
        assert!(matches!(
            error,
            CommandServiceError::CommandCallerMismatch { command_id: id, expected, actual }
                if id == command_id
                    && expected == CallerId("caller-owner".to_owned())
                    && actual == CallerId("caller-other".to_owned())
        ));
    }
}
