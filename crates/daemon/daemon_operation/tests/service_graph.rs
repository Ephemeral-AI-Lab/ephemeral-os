use std::path::PathBuf;
use std::sync::Arc;

use daemon_operation::command::{CommandCallContext, CommandOperationService, ExecCommandInput};
use daemon_operation::workspace_remount::{
    CommandRemountCoordinator, RemountWorkspaceSession, WorkspaceRemountService,
};
use daemon_operation::workspace_session::WorkspaceSessionService;
use daemon_operation::DaemonOperations;
use workspace::{
    CallerId, CaptureChangesRequest, CreateWorkspaceRequest, DestroyWorkspaceRequest,
    LatestSnapshotRequest, RemountWorkspaceRequest, WorkspaceError, WorkspaceHandle,
    WorkspaceRuntimeHooks, WorkspaceRuntimeService, WorkspaceSessionId,
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
            latest_snapshot: Box::new(|_request: LatestSnapshotRequest| {
                Err(WorkspaceError::SnapshotAcquire {
                    source: "not configured".to_owned(),
                })
            }),
        },
    ))
}

#[test]
fn daemon_operations_exposes_only_command_as_external_lane() {
    let workspace = workspace_session();
    let command = Arc::new(CommandOperationService::new(
        Arc::clone(&workspace),
        command::CommandConfig::default(),
    ));
    let remount_workspace: Arc<dyn RemountWorkspaceSession> = workspace.clone();
    let remount_command: Arc<dyn CommandRemountCoordinator> = command.clone();
    let remount = Arc::new(WorkspaceRemountService::new(
        remount_workspace,
        remount_command,
    ));

    let _ = remount;
    let operations = DaemonOperations::new(Arc::clone(&command));

    assert!(Arc::ptr_eq(&operations.command, &command));
}

#[test]
fn command_contract_keeps_roots_and_call_context_separate() {
    let input = ExecCommandInput {
        caller_id: CallerId("caller-1".to_owned()),
        workspace_root: PathBuf::from("/workspace"),
        workspace_session_id: Some(WorkspaceSessionId("workspace-1".to_owned())),
        cmd: "pwd".to_owned(),
        cwd: None,
        timeout_seconds: None,
        yield_time_ms: Some(100),
    };
    let context = CommandCallContext {
        caller_id: input.caller_id.clone(),
    };

    assert_eq!(input.workspace_root, PathBuf::from("/workspace"));
    assert_eq!(
        input.workspace_session_id,
        Some(WorkspaceSessionId("workspace-1".to_owned()))
    );
    assert_eq!(context.caller_id, CallerId("caller-1".to_owned()));
}
