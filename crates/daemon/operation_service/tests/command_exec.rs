mod support;

use std::path::PathBuf;
use std::sync::Arc;

use operation_service::command::{
    CommandFinalizePolicy, CommandId, CommandServiceError, CommandStatus, ExecCommandInput,
    OperationTraceContext,
};
use workspace::{CallerId, NetworkMode, WorkspaceId};

use support::{
    assert_private_host_create_request, build_services, create_request, workspace_handle,
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
    assert_eq!(
        env.command.registry().workspace_for(&command_id),
        Some(WorkspaceId("workspace-session".to_owned()))
    );
    let active = env
        .command
        .process_store()
        .active(&command_id)
        .expect("active command is inserted");
    assert_eq!(active.caller_id, CallerId("caller-1".to_owned()));
    assert_eq!(
        active.finalize_policy,
        CommandFinalizePolicy::Session {
            workspace_id: WorkspaceId("workspace-session".to_owned())
        }
    );
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
    assert_private_host_create_request(&create_requests[0], "caller-1", &workspace_root);
    assert!(fake.destroy_calls().is_empty());
    assert_eq!(
        env.command.registry().workspace_for(&command_id),
        Some(WorkspaceId("workspace-one-shot".to_owned()))
    );
    let active = env
        .command
        .process_store()
        .active(&command_id)
        .expect("active command is inserted");
    assert_eq!(
        active.finalize_policy,
        CommandFinalizePolicy::OneShotPublishThenDestroy {
            workspace_id: WorkspaceId("workspace-one-shot".to_owned())
        }
    );
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

    assert!(matches!(
        error,
        CommandServiceError::WorkspaceRootMismatch { expected, actual }
            if expected == PathBuf::from("/workspace/session")
                && actual == PathBuf::from("/workspace/other")
    ));
    assert_eq!(
        env.command.process_store().allocate_command_id(),
        CommandId("cmd_1".to_owned())
    );
}
