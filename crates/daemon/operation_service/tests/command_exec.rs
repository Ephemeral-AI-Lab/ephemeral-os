mod support;

use std::path::{Path, PathBuf};
use std::sync::Arc;

use command::yield_wait_loop::WaitOutcome;
use operation_service::command::{
    CommandCallContext, CommandFinalizationOutcome, CommandFinalizedPolicy, CommandId,
    CommandServiceError, CommandStatus, ExecCommandInput, OperationTraceContext, PollCommandInput,
};
use workspace::{CallerId, WorkspaceId, WorkspaceProfile};

use support::{
    assert_private_create_request, build_services, build_services_with_launch_driver,
    create_request, success_exit, workspace_handle, workspace_handle_unavailable_launch,
    workspace_handle_without_launch, FakeLaunchDriver, FakeWorkspaceService,
};

fn exec_input(
    caller_id: &str,
    workspace_root: PathBuf,
    workspace_session_id: Option<WorkspaceId>,
) -> ExecCommandInput {
    ExecCommandInput {
        caller_id: CallerId(caller_id.to_owned()),
        workspace_root,
        workspace_session_id,
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
        WorkspaceProfile::HostCompatible,
    )));
    let env = build_services(Arc::clone(&fake));
    let handler = env
        .workspace
        .create_workspace_session(create_request("caller-1", workspace_root.clone()))
        .expect("session create succeeds");
    let create_count_before_exec = fake.create_requests().len();

    let output = env
        .command
        .exec_command(
            exec_input(
                "caller-1",
                workspace_root,
                Some(handler.workspace_session_id.clone()),
            ),
            context("caller-1"),
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
        WorkspaceProfile::HostCompatible,
    )));
    let env = build_services(Arc::clone(&fake));

    let output = env
        .command
        .exec_command(
            exec_input("caller-1", workspace_root.clone(), None),
            context("caller-1"),
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
        WorkspaceProfile::HostCompatible,
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
fn command_exec_rejects_context_caller_mismatch_before_workspace_create() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let workspace_root = PathBuf::from("/workspace/one-shot");
    let env = build_services(Arc::clone(&fake));

    let error = env
        .command
        .exec_command(
            exec_input("caller-1", workspace_root.clone(), None),
            context("caller-2"),
        )
        .expect_err("caller mismatch rejects before one-shot create");

    assert!(matches!(
        error,
        CommandServiceError::InvalidCommand { message }
            if message.contains("exec caller must match command context")
    ));
    assert!(fake.create_requests().is_empty());

    fake.push_create_result(Ok(workspace_handle(
        "workspace-one-shot",
        "caller-1",
        "lease-1",
        workspace_root.clone(),
        WorkspaceProfile::HostCompatible,
    )));
    let output = env
        .command
        .exec_command(
            exec_input("caller-1", workspace_root, None),
            context("caller-1"),
        )
        .expect("subsequent valid exec succeeds");
    assert_eq!(output.command_id, Some(CommandId("cmd_1".to_owned())));
}

#[test]
fn command_exec_spawn_failure_destroys_created_one_shot_workspace() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let workspace_root = PathBuf::from("/workspace/one-shot");
    fake.push_create_result(Ok(workspace_handle(
        "workspace-one-shot",
        "caller-1",
        "lease-1",
        workspace_root.clone(),
        WorkspaceProfile::HostCompatible,
    )));
    let launch_driver = Arc::new(FakeLaunchDriver::new());
    launch_driver.push_spawn_error(CommandServiceError::CommandIo {
        command_id: CommandId("cmd_1".to_owned()),
        error: "spawn failed".to_owned(),
    });
    let env = build_services_with_launch_driver(Arc::clone(&fake), launch_driver);

    let error = env
        .command
        .exec_command(
            exec_input("caller-1", workspace_root, None),
            context("caller-1"),
        )
        .expect_err("spawn failure rejects exec");

    match error {
        CommandServiceError::CommandIo { command_id, error } => {
            assert_eq!(command_id, CommandId("cmd_1".to_owned()));
            assert_eq!(error, "spawn failed");
        }
        other => panic!("expected command io error, got {other:?}"),
    }
    assert_eq!(
        fake.destroy_calls(),
        vec![WorkspaceId("workspace-one-shot".to_owned())]
    );
    assert!(
        !env.command.config().scratch_root.join("cmd_1").exists(),
        "spawn failure should clean up unretained command artifacts"
    );
}

#[test]
fn command_exec_spawn_failure_keeps_session_workspace_alive() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let workspace_root = PathBuf::from("/workspace/session");
    fake.push_create_result(Ok(workspace_handle(
        "workspace-session",
        "caller-1",
        "lease-1",
        workspace_root.clone(),
        WorkspaceProfile::HostCompatible,
    )));
    let launch_driver = Arc::new(FakeLaunchDriver::new());
    launch_driver.push_spawn_error(CommandServiceError::CommandIo {
        command_id: CommandId("cmd_1".to_owned()),
        error: "spawn failed".to_owned(),
    });
    let env = build_services_with_launch_driver(Arc::clone(&fake), launch_driver);
    let handler = env
        .workspace
        .create_workspace_session(create_request("caller-1", workspace_root.clone()))
        .expect("session create succeeds");

    let error = env
        .command
        .exec_command(
            exec_input(
                "caller-1",
                workspace_root,
                Some(handler.workspace_session_id.clone()),
            ),
            context("caller-1"),
        )
        .expect_err("spawn failure rejects session exec");

    assert!(matches!(
        error,
        CommandServiceError::CommandIo { command_id, error }
            if command_id == CommandId("cmd_1".to_owned()) && error == "spawn failed"
    ));
    assert!(fake.destroy_calls().is_empty());
    assert!(
        !env.command.config().scratch_root.join("cmd_1").exists(),
        "session spawn failure should clean up unretained command artifacts"
    );
}

#[test]
fn command_exec_passes_workspace_entry_to_spawn_paths() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let workspace_root = PathBuf::from("/workspace/one-shot");
    fake.push_create_result(Ok(workspace_handle(
        "workspace-one-shot",
        "caller-1",
        "lease-1",
        workspace_root.clone(),
        WorkspaceProfile::Isolated,
    )));
    let launch_driver = Arc::new(FakeLaunchDriver::new());
    let env = build_services_with_launch_driver(Arc::clone(&fake), launch_driver.clone());
    let mut input = exec_input("caller-1", workspace_root.clone(), None);
    input.cwd = Some(PathBuf::from("/workspace/one-shot/src"));
    input.timeout_seconds = Some(2.5);

    let output = env
        .command
        .exec_command(input, context("caller-1"))
        .expect("one-shot command exec succeeds");

    assert_eq!(output.command_id, Some(CommandId("cmd_1".to_owned())));
    let observations = launch_driver.spawn_observations();
    assert_eq!(observations.len(), 1);
    let observation = &observations[0];
    assert_eq!(observation.spec_id, "cmd_1");
    assert_eq!(observation.spec_caller_id, "caller-1");
    assert_eq!(observation.spec_command, "printf ok");
    assert_eq!(
        observation.spec_cwd,
        Some(PathBuf::from("/workspace/one-shot/src"))
    );
    assert_eq!(observation.spec_timeout_seconds, Some(2.5));
    assert_eq!(
        observation.request_path,
        env.command
            .config()
            .scratch_root
            .join("cmd_1")
            .join("command-request.json")
    );
    assert_eq!(
        observation.output_path,
        env.command
            .config()
            .scratch_root
            .join("cmd_1")
            .join("runner-result.json")
    );
    assert_eq!(
        observation.final_path,
        env.command
            .config()
            .scratch_root
            .join("cmd_1")
            .join("final.json")
    );
    assert_eq!(
        observation.transcript_path,
        env.command
            .config()
            .scratch_root
            .join("cmd_1")
            .join("transcript.log")
    );
    assert_eq!(
        observation.transcript_timestamp_timezone,
        env.command.config().transcript_timestamp_timezone
    );
    assert_eq!(
        observation.output_drain_grace_ms,
        env.command.config().output_drain_grace_ms
    );
    let entry = &observation.workspace_entry;
    assert_eq!(&entry.workspace_root, &workspace_root);
    assert_eq!(entry.layer_paths.as_slice(), &[PathBuf::from("/lower/one")]);
    assert_eq!(
        entry.upperdir.file_name().and_then(|name| name.to_str()),
        Some("upper")
    );
    assert_eq!(
        entry.workdir.file_name().and_then(|name| name.to_str()),
        Some("work")
    );
    assert_eq!(entry.ns_fds.user, 10);
    assert_eq!(entry.ns_fds.mnt, 11);
    assert_eq!(entry.ns_fds.pid, 12);
    assert_eq!(entry.ns_fds.net, Some(13));
    assert!(entry.cgroup_path.is_none());
}

#[test]
fn command_exec_missing_launch_material_destroys_one_shot_without_spawn() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let workspace_root = PathBuf::from("/workspace/one-shot");
    fake.push_create_result(Ok(workspace_handle_without_launch(
        "workspace-one-shot",
        "caller-1",
        "lease-1",
        workspace_root.clone(),
        WorkspaceProfile::HostCompatible,
    )));
    let launch_driver = Arc::new(FakeLaunchDriver::new());
    let env = build_services_with_launch_driver(Arc::clone(&fake), launch_driver.clone());

    let error = env
        .command
        .exec_command(
            exec_input("caller-1", workspace_root, None),
            context("caller-1"),
        )
        .expect_err("missing launch material rejects exec");

    assert!(matches!(
        error,
        CommandServiceError::InvalidCommand { message }
            if message.contains("lacks workspace entry material")
    ));
    assert!(launch_driver.spawn_observations().is_empty());
    assert_eq!(
        fake.destroy_calls(),
        vec![WorkspaceId("workspace-one-shot".to_owned())]
    );
    assert!(
        !env.command.config().scratch_root.join("cmd_1").exists(),
        "missing launch material should not leave command artifacts"
    );
}

#[test]
fn command_exec_unavailable_workspace_launch_destroys_one_shot_without_spawn() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let workspace_root = PathBuf::from("/workspace/one-shot");
    fake.push_create_result(Ok(workspace_handle_unavailable_launch(
        "workspace-one-shot",
        "caller-1",
        "lease-1",
        workspace_root.clone(),
        WorkspaceProfile::HostCompatible,
    )));
    let launch_driver = Arc::new(FakeLaunchDriver::new());
    let env = build_services_with_launch_driver(Arc::clone(&fake), launch_driver.clone());

    let error = env
        .command
        .exec_command(
            exec_input("caller-1", workspace_root, None),
            context("caller-1"),
        )
        .expect_err("unavailable workspace launch rejects exec");

    assert!(matches!(
        error,
        CommandServiceError::InvalidCommand { message }
            if message.contains("workspace entry context is incomplete")
    ));
    assert!(launch_driver.spawn_observations().is_empty());
    assert_eq!(
        fake.destroy_calls(),
        vec![WorkspaceId("workspace-one-shot".to_owned())]
    );
    assert!(
        !env.command.config().scratch_root.join("cmd_1").exists(),
        "unavailable workspace launch should not leave command artifacts"
    );
}

#[test]
fn command_exec_artifact_directory_failure_destroys_one_shot_without_spawn() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let workspace_root = PathBuf::from("/workspace/one-shot");
    fake.push_create_result(Ok(workspace_handle(
        "workspace-one-shot",
        "caller-1",
        "lease-1",
        workspace_root.clone(),
        WorkspaceProfile::HostCompatible,
    )));
    let launch_driver = Arc::new(FakeLaunchDriver::new());
    let env = build_services_with_launch_driver(Arc::clone(&fake), launch_driver.clone());
    std::fs::write(
        env.command.config().scratch_root.clone(),
        b"not a directory",
    )
    .expect("scratch root file fixture is written");

    let error = env
        .command
        .exec_command(
            exec_input("caller-1", workspace_root, None),
            context("caller-1"),
        )
        .expect_err("artifact directory failure rejects exec");

    assert!(matches!(
        error,
        CommandServiceError::CommandIo { command_id, error }
            if command_id == CommandId("cmd_1".to_owned())
                && error.contains("prepare command artifact directory")
    ));
    assert!(launch_driver.spawn_observations().is_empty());
    assert_eq!(
        fake.destroy_calls(),
        vec![WorkspaceId("workspace-one-shot".to_owned())]
    );
}

#[test]
fn command_exec_initial_running_yield_returns_wait_loop_output() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let workspace_root = PathBuf::from("/workspace/one-shot");
    fake.push_create_result(Ok(workspace_handle(
        "workspace-one-shot",
        "caller-1",
        "lease-1",
        workspace_root.clone(),
        WorkspaceProfile::HostCompatible,
    )));
    let launch_driver = Arc::new(FakeLaunchDriver::new());
    launch_driver.push_outcome(WaitOutcome::Running("hello from wait\n".to_owned()));
    let env = build_services_with_launch_driver(fake, launch_driver);

    let output = env
        .command
        .exec_command(
            exec_input("caller-1", workspace_root, None),
            context("caller-1"),
        )
        .expect("exec returns initial running yield");

    assert_eq!(output.status, CommandStatus::Running);
    assert_eq!(output.output.stdout, "hello from wait\n");
}

#[test]
fn command_exec_initial_completed_session_returns_finalized_metadata() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let workspace_root = PathBuf::from("/workspace/session");
    fake.push_create_result(Ok(workspace_handle(
        "workspace-session",
        "caller-1",
        "lease-1",
        workspace_root.clone(),
        WorkspaceProfile::HostCompatible,
    )));
    let launch_driver = Arc::new(FakeLaunchDriver::new());
    launch_driver.push_outcome(WaitOutcome::Completed(success_exit("session done\n")));
    let env = build_services_with_launch_driver(Arc::clone(&fake), launch_driver);
    let handler = env
        .workspace
        .create_workspace_session(create_request("caller-1", workspace_root.clone()))
        .expect("session create succeeds");

    let output = env
        .command
        .exec_command(
            exec_input(
                "caller-1",
                workspace_root,
                Some(handler.workspace_session_id.clone()),
            ),
            context("caller-1"),
        )
        .expect("session command completes during initial yield");

    let command_id = output.command_id.expect("command id is returned");
    assert_eq!(output.status, CommandStatus::Completed);
    assert_eq!(output.exit_code, Some(0));
    assert_eq!(output.output.stdout, "session done\n");
    let finalized = output.finalized.expect("session metadata is returned");
    assert_eq!(finalized.policy, CommandFinalizedPolicy::Session);
    assert_eq!(
        finalized.outcome,
        CommandFinalizationOutcome::SessionComplete
    );
    assert!(fake.destroy_calls().is_empty());

    let poll = env
        .command
        .poll(
            PollCommandInput {
                command_id: command_id.clone(),
                last_n_lines: None,
            },
            context("caller-1"),
        )
        .expect("owner can poll completed session command");
    assert_eq!(poll.command_id, command_id);
    assert_eq!(poll.status, CommandStatus::Completed);
}

#[test]
fn command_exec_rejects_workspace_root_mismatch_before_command_allocation() {
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(workspace_handle(
        "workspace-session",
        "caller-1",
        "lease-1",
        PathBuf::from("/workspace/session"),
        WorkspaceProfile::HostCompatible,
    )));
    let env = build_services(Arc::clone(&fake));
    let handler = env
        .workspace
        .create_workspace_session(create_request(
            "caller-1",
            PathBuf::from("/workspace/session"),
        ))
        .expect("session create succeeds");

    let error = env
        .command
        .exec_command(
            exec_input(
                "caller-1",
                PathBuf::from("/workspace/other"),
                Some(handler.workspace_session_id),
            ),
            context("caller-1"),
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
        .command
        .exec_command(
            exec_input(
                "caller-1",
                PathBuf::from("/workspace/session"),
                Some(WorkspaceId("workspace-session".to_owned())),
            ),
            context("caller-1"),
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
