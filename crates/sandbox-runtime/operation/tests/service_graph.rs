use std::sync::Arc;

use sandbox_protocol::OperationExecutionSpace;
use sandbox_runtime::command::{CommandOperationService, ExecCommandInput};
use sandbox_runtime::workspace_remount::{
    CommandRemountCoordinator, RemountWorkspaceSession, WorkspaceRemountService,
};
use sandbox_runtime::workspace_session::WorkspaceSessionService;
use sandbox_runtime::SandboxRuntimeOperations;
use sandbox_runtime_workspace::{
    CaptureChangesRequest, CreateWorkspaceRequest, DestroyWorkspaceRequest,
    RemountWorkspaceRequest, WorkspaceError, WorkspaceHandle, WorkspaceRuntimeHooks,
    WorkspaceRuntimeService, WorkspaceSessionId,
};

fn workspace_session() -> Arc<WorkspaceSessionService> {
    Arc::new(WorkspaceSessionService::new(noop_workspace_runtime()))
}

fn noop_workspace_runtime() -> Arc<WorkspaceRuntimeService> {
    Arc::new(WorkspaceRuntimeService::from_hooks_for_test(
        WorkspaceRuntimeHooks {
            create_workspace: Box::new(|_request: CreateWorkspaceRequest| {
                Err(WorkspaceError::Setup {
                    step: "not configured".to_owned(),
                })
            }),
            capture_changes: Box::new(
                |_handle: &WorkspaceHandle, _request: CaptureChangesRequest| {
                    Err(WorkspaceError::Capture {
                        message: "not configured".to_owned(),
                    })
                },
            ),
            remount_workspace: Box::new(
                |_handle: &WorkspaceHandle, _request: RemountWorkspaceRequest| {
                    Err(WorkspaceError::Setup {
                        step: "not configured".to_owned(),
                    })
                },
            ),
            destroy_workspace: Box::new(
                |_handle: WorkspaceHandle, _request: DestroyWorkspaceRequest| {
                    Err(WorkspaceError::Setup {
                        step: "not configured".to_owned(),
                    })
                },
            ),
            latest_snapshot: Box::new(|| {
                Err(WorkspaceError::SnapshotAcquire {
                    source: "not configured".to_owned(),
                })
            }),
        },
    ))
}

#[test]
fn runtime_operations_exposes_only_command_as_external_lane() {
    let workspace = workspace_session();
    let command = Arc::new(CommandOperationService::new(
        Arc::clone(&workspace),
        sandbox_runtime_command::CommandConfig::default(),
    ));
    let remount_workspace: Arc<dyn RemountWorkspaceSession> = workspace.clone();
    let remount_command: Arc<dyn CommandRemountCoordinator> = command.clone();
    let remount = Arc::new(WorkspaceRemountService::new(
        remount_workspace,
        remount_command,
    ));

    let _ = remount;
    let operations = SandboxRuntimeOperations::new(Arc::clone(&command));

    assert!(Arc::ptr_eq(&operations.command, &command));
}

#[test]
fn command_contract_keeps_session_selector_in_exec_input() {
    let input = ExecCommandInput {
        workspace_session_id: WorkspaceSessionId("workspace-1".to_owned()),
        cmd: "pwd".to_owned(),
        timeout_seconds: None,
        yield_time_ms: Some(100),
    };

    assert_eq!(
        input.workspace_session_id,
        WorkspaceSessionId("workspace-1".to_owned())
    );
}

#[test]
fn operation_catalog_exports_runtime_command_operations() {
    let catalog = sandbox_runtime::operation_catalog();
    let names = catalog
        .operations
        .iter()
        .map(|spec| spec.name)
        .collect::<Vec<_>>();

    assert_eq!(
        catalog.operation_execution_space,
        OperationExecutionSpace::Runtime
    );
    assert_eq!(
        names,
        [
            "exec_command",
            "write_command_stdin",
            "poll_command",
            "read_command_lines",
            "cancel_command"
        ]
    );
}

#[test]
fn operation_catalog_cli_metadata_uses_runtime_space() {
    let catalog = sandbox_runtime::operation_catalog();

    assert!(catalog.operations.iter().all(|spec| {
        spec.cli
            .map(|cli| {
                cli.path.first() == Some(&"runtime")
                    && cli
                        .usage
                        .starts_with("sandbox-cli runtime --sandbox-id ID ")
                    && cli.examples.iter().all(|example| {
                        example.starts_with("sandbox-cli runtime --sandbox-id sbox-1 ")
                            && !example.contains("daemon")
                    })
            })
            .unwrap_or(true)
    }));
}
