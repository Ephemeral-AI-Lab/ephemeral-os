use std::path::PathBuf;
use std::sync::Arc;

use operation_service::command::{
    CommandCallContext, CommandFinalizationOptions, CommandId, CommandOperationService,
    ExecCommandInput, OperationTraceContext,
};
use operation_service::workspace_manager::WorkspaceManagerService;
use operation_service::workspace_remount::{WorkspaceRemountOptions, WorkspaceRemountService};
use operation_service::OperationServices;
use workspace::{
    CallerId, CaptureChangesRequest, CapturedWorkspaceChanges, CreateWorkspaceRequest,
    DestroyWorkspaceRequest, DestroyWorkspaceResult, LatestSnapshotRequest, ReadonlySnapshotHandle,
    RemountWorkspaceRequest, RemountWorkspaceResult, WorkspaceError, WorkspaceHandle, WorkspaceId,
    WorkspaceService,
};

struct NoopWorkspaceService;

impl WorkspaceService for NoopWorkspaceService {
    fn create_workspace(
        &self,
        _request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceHandle, WorkspaceError> {
        Err(WorkspaceError::Setup {
            step: "not configured".to_owned(),
        })
    }

    fn capture_changes(
        &self,
        _handle: &WorkspaceHandle,
        _request: CaptureChangesRequest,
    ) -> Result<CapturedWorkspaceChanges, WorkspaceError> {
        Err(WorkspaceError::Capture {
            message: "not configured".to_owned(),
        })
    }

    fn remount_workspace(
        &self,
        _handle: &WorkspaceHandle,
        _request: RemountWorkspaceRequest,
    ) -> Result<RemountWorkspaceResult, WorkspaceError> {
        Err(WorkspaceError::Setup {
            step: "not configured".to_owned(),
        })
    }

    fn destroy_workspace(
        &self,
        _handle: WorkspaceHandle,
        _request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceError> {
        Err(WorkspaceError::Setup {
            step: "not configured".to_owned(),
        })
    }

    fn latest_snapshot(
        &self,
        _request: LatestSnapshotRequest,
    ) -> Result<ReadonlySnapshotHandle, WorkspaceError> {
        Err(WorkspaceError::SnapshotAcquire {
            source: "not configured".to_owned(),
        })
    }
}

fn workspace_manager() -> Arc<WorkspaceManagerService> {
    Arc::new(WorkspaceManagerService::new(Arc::new(NoopWorkspaceService)))
}

#[test]
fn operation_services_wires_top_level_domains() {
    let workspace = workspace_manager();
    let command = Arc::new(CommandOperationService::new(
        Arc::clone(&workspace),
        command::CommandConfig::default(),
    ));
    let remount = Arc::new(WorkspaceRemountService::new(
        Arc::clone(&workspace),
        Arc::clone(&command),
        WorkspaceRemountOptions::default(),
    ));

    let services = OperationServices::new(
        Arc::clone(&workspace),
        Arc::clone(&command),
        Arc::clone(&remount),
    );

    assert!(Arc::ptr_eq(&services.workspace, &workspace));
    assert!(Arc::ptr_eq(&services.command, &command));
    assert!(Arc::ptr_eq(&services.remount, &remount));
}

#[test]
fn command_contract_keeps_roots_and_trace_context_separate() {
    let input = ExecCommandInput {
        caller_id: CallerId("caller-1".to_owned()),
        workspace_root: PathBuf::from("/workspace"),
        workspace_id: Some(WorkspaceId("workspace-1".to_owned())),
        cmd: "pwd".to_owned(),
        cwd: None,
        timeout_seconds: None,
        yield_time_ms: Some(100),
    };
    let context = CommandCallContext {
        caller_id: input.caller_id.clone(),
        trace: OperationTraceContext::default(),
    };

    assert_eq!(input.workspace_root, PathBuf::from("/workspace"));
    assert_eq!(
        input.workspace_id,
        Some(WorkspaceId("workspace-1".to_owned()))
    );
    assert_eq!(context.caller_id, CallerId("caller-1".to_owned()));
}

#[test]
fn command_service_retains_one_shot_finalization_options() {
    let workspace = workspace_manager();
    let config = command::CommandConfig::default();
    let options = CommandFinalizationOptions {
        one_shot_capture: layerstack::service::BoundedCaptureOptions {
            materialize_payloads: false,
            ..layerstack::service::BoundedCaptureOptions::default()
        },
        one_shot_publish: layerstack::CommitOptions::new(3),
    };

    let command = CommandOperationService::with_finalization_options(
        Arc::clone(&workspace),
        config.clone(),
        options,
    );

    assert!(Arc::ptr_eq(command.workspace(), &workspace));
    assert_eq!(command.config(), &config);
    assert_eq!(command.finalization_options(), &options);
    assert!(command
        .registry()
        .workspace_for(&CommandId("missing".to_owned()))
        .is_none());
    assert_eq!(
        command.process_store().allocate_command_id(),
        CommandId("cmd_1".to_owned())
    );
}

#[test]
fn workspace_remount_options_are_constructor_owned() {
    let workspace = workspace_manager();
    let command = Arc::new(CommandOperationService::new(
        Arc::clone(&workspace),
        command::CommandConfig::default(),
    ));
    let options = WorkspaceRemountOptions {
        live_quiesce_timeout_ms: 7_500,
    };
    let remount =
        WorkspaceRemountService::new(Arc::clone(&workspace), Arc::clone(&command), options);

    assert!(Arc::ptr_eq(remount.workspace(), &workspace));
    assert!(Arc::ptr_eq(remount.command(), &command));
    assert_eq!(remount.options(), options);
}
