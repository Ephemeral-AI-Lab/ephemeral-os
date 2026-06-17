mod support;

use std::path::{Path, PathBuf};
use std::sync::Arc;

use operation_service::command::{
    CommandCallContext, CommandId, CommandServiceError, CommandStatus, ExecCommandInput,
    OperationTraceContext, PollCommandInput,
};
use workspace::{CallerId, NetworkMode, WorkspaceId};

use support::{
    assert_private_create_request, build_services, create_request, workspace_handle,
    FakeWorkspaceService,
};

fn exec_input(
    caller_id: &str,
    workspace_root: PathBuf,
    workspace_id: Option<WorkspaceId>,
) -> ExecCommandInput {
    ExecCommandInput {
        caller_id: CallerId(caller_id.to_owned()),
        workspace_root,
        workspace_id,
        cmd: "printf ok".to_owned(),
        cwd: None,
        timeout_seconds: None,
        yield_time_ms: Some(0),
    }
}

#[test]
fn command_exec_some_uses_resolved_session_without_workspace_create_or_destroy() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let workspace_root = PathBuf::from("/workspace/session");
    fake.push_create_result(Ok(workspace_handle(
        "workspace-session",
        "caller-1",
        "lease-1",
        workspace_root.clone(),
        NetworkMode::Host,
    )));
    let env = build_services(Arc::clone(&fake));
    let handler = env
        .workspace
        .create(create_request("caller-1", workspace_root.clone()))
        .expect("session create succeeds");
    let create_count_before_exec = fake.create_requests().len();

    let output = env
        .services
        .exec_command(
            exec_input(
                "caller-1",
                workspace_root,
                Some(handler.workspace_id.clone()),
            ),
            OperationTraceContext,
        )
        .expect("session command exec succeeds");

    let command_id = output.command_id.expect("running command id is returned");
    assert_eq!(output.status, CommandStatus::Running);
    assert_eq!(fake.create_requests().len(), create_count_before_exec);
    assert!(fake.destroy_calls().is_empty());
    let poll = env
        .command
        .poll(
            PollCommandInput {
                command_id: command_id.clone(),
                last_n_lines: Some(10),
            },
            context("caller-1"),
        )
        .expect("owner can poll session command");
    assert_eq!(poll.command_id, command_id);
    assert_eq!(poll.status, CommandStatus::Running);
}

#[test]
fn command_exec_none_creates_private_host_workspace_and_binds_it() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let workspace_root = PathBuf::from("/workspace/one-shot");
    fake.push_create_result(Ok(workspace_handle(
        "workspace-one-shot",
        "caller-1",
        "lease-1",
        workspace_root.clone(),
        NetworkMode::Host,
    )));
    let env = build_services(Arc::clone(&fake));

    let output = env
        .services
        .exec_command(
            exec_input("caller-1", workspace_root.clone(), None),
            OperationTraceContext,
        )
        .expect("one-shot command exec succeeds");

    let command_id = output.command_id.expect("running command id is returned");
    assert_eq!(output.status, CommandStatus::Running);
    assert_eq!(output.exit_code, None);
    let create_requests = fake.create_requests();
    assert_eq!(create_requests.len(), 1);
    assert_private_create_request(
        &create_requests[0],
        "caller-1",
        &workspace_root,
        NetworkMode::Host,
    );
    assert!(fake.destroy_calls().is_empty());
    let poll = env
        .command
        .poll(
            PollCommandInput {
                command_id: command_id.clone(),
                last_n_lines: Some(10),
            },
            context("caller-1"),
        )
        .expect("owner can poll one-shot command");
    assert_eq!(poll.command_id, command_id);
    assert_eq!(poll.status, CommandStatus::Running);
}

#[test]
fn command_exec_rejects_workspace_root_mismatch_before_command_allocation() {
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(workspace_handle(
        "workspace-session",
        "caller-1",
        "lease-1",
        PathBuf::from("/workspace/session"),
        NetworkMode::Host,
    )));
    let env = build_services(Arc::clone(&fake));
    let handler = env
        .workspace
        .create(create_request(
            "caller-1",
            PathBuf::from("/workspace/session"),
        ))
        .expect("session create succeeds");

    let error = env
        .services
        .exec_command(
            exec_input(
                "caller-1",
                PathBuf::from("/workspace/other"),
                Some(handler.workspace_id),
            ),
            OperationTraceContext,
        )
        .expect_err("root mismatch is rejected");

    match error {
        CommandServiceError::WorkspaceRootMismatch { expected, actual } => {
            assert_eq!(expected.as_path(), Path::new("/workspace/session"));
            assert_eq!(actual.as_path(), Path::new("/workspace/other"));
        }
        other => panic!("expected workspace root mismatch, got {other:?}"),
    }
    let output = env
        .services
        .exec_command(
            exec_input(
                "caller-1",
                PathBuf::from("/workspace/session"),
                Some(WorkspaceId("workspace-session".to_owned())),
            ),
            OperationTraceContext,
        )
        .expect("subsequent valid exec succeeds");
    assert_eq!(output.command_id, Some(CommandId("cmd_1".to_owned())));
}

fn context(caller_id: &str) -> CommandCallContext {
    CommandCallContext {
        caller_id: CallerId(caller_id.to_owned()),
        trace: OperationTraceContext,
    }
}
