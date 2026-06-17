use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use operation_service::workspace::{WorkspaceManagerError, WorkspaceManagerService};
use workspace::{
    BaseRevision, CallerId, CaptureChangesRequest, CapturedWorkspaceChanges,
    CreateWorkspaceRequest, DestroyWorkspaceRequest, DestroyWorkspaceResult, LatestSnapshotRequest,
    LayerStackSnapshotRef, LeaseId, NetworkMode, ReadonlySnapshotHandle, RemountWorkspaceRequest,
    RemountWorkspaceResult, WorkspaceError, WorkspaceHandle, WorkspaceId, WorkspaceService,
};

struct FakeWorkspaceService {
    create_results: Mutex<VecDeque<Result<WorkspaceHandle, WorkspaceError>>>,
    destroy_results: Mutex<VecDeque<Result<DestroyWorkspaceResult, WorkspaceError>>>,
    destroy_calls: Mutex<Vec<WorkspaceId>>,
}

impl FakeWorkspaceService {
    fn new() -> Self {
        Self {
            create_results: Mutex::new(VecDeque::new()),
            destroy_results: Mutex::new(VecDeque::new()),
            destroy_calls: Mutex::new(Vec::new()),
        }
    }

    fn push_create_result(&self, result: Result<WorkspaceHandle, WorkspaceError>) {
        self.create_results.lock().unwrap().push_back(result);
    }

    fn push_destroy_result(&self, result: Result<DestroyWorkspaceResult, WorkspaceError>) {
        self.destroy_results.lock().unwrap().push_back(result);
    }

    fn destroy_calls(&self) -> Vec<WorkspaceId> {
        self.destroy_calls.lock().unwrap().clone()
    }
}

impl WorkspaceService for FakeWorkspaceService {
    fn create_workspace(
        &self,
        _request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceHandle, WorkspaceError> {
        self.create_results
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or_else(|| {
                Err(WorkspaceError::Setup {
                    step: "create result not configured".to_owned(),
                })
            })
    }

    fn capture_changes(
        &self,
        _handle: &WorkspaceHandle,
        _request: CaptureChangesRequest,
    ) -> Result<CapturedWorkspaceChanges, WorkspaceError> {
        Err(WorkspaceError::Capture {
            message: "capture result not configured".to_owned(),
        })
    }

    fn remount_workspace(
        &self,
        _handle: &WorkspaceHandle,
        _request: RemountWorkspaceRequest,
    ) -> Result<RemountWorkspaceResult, WorkspaceError> {
        Err(WorkspaceError::Setup {
            step: "remount result not configured".to_owned(),
        })
    }

    fn destroy_workspace(
        &self,
        handle: WorkspaceHandle,
        _request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceError> {
        self.destroy_calls.lock().unwrap().push(handle.id.clone());
        self.destroy_results
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or_else(|| Ok(destroy_result(&handle)))
    }

    fn latest_snapshot(
        &self,
        _request: LatestSnapshotRequest,
    ) -> Result<ReadonlySnapshotHandle, WorkspaceError> {
        Err(WorkspaceError::SnapshotAcquire {
            source: "latest snapshot not configured".to_owned(),
        })
    }
}

fn manager_with(fake: &Arc<FakeWorkspaceService>) -> WorkspaceManagerService {
    WorkspaceManagerService::new(fake.clone())
}

fn create_request(caller_id: &str) -> CreateWorkspaceRequest {
    CreateWorkspaceRequest {
        owner: CallerId(caller_id.to_owned()),
        workspace_root: PathBuf::from("/workspace"),
        network: NetworkMode::Host,
    }
}

fn workspace_handle(workspace_id: &str, caller_id: &str, lease_id: &str) -> WorkspaceHandle {
    let snapshot = LayerStackSnapshotRef {
        lease_id: LeaseId(lease_id.to_owned()),
        manifest_version: 1,
        root_hash: "root".to_owned(),
        layer_paths: vec![PathBuf::from("/lower/one")],
    };
    WorkspaceHandle {
        id: WorkspaceId(workspace_id.to_owned()),
        owner: CallerId(caller_id.to_owned()),
        workspace_root: PathBuf::from("/workspace"),
        network: NetworkMode::Host,
        base_revision: BaseRevision {
            version: 1,
            root_hash: "root".to_owned(),
            layer_count: 1,
        },
        snapshot,
    }
}

fn destroy_result(handle: &WorkspaceHandle) -> DestroyWorkspaceResult {
    DestroyWorkspaceResult {
        workspace_id: handle.id.clone(),
        owner: handle.owner.clone(),
        cancelled_commands: 0,
        evicted_upperdir_bytes: 0,
        lifetime_s: 0.0,
        lease_released: Some(true),
        lease_release_error: None,
        active_leases_after: 0,
    }
}

#[test]
fn workspace_manager_resolve_validates_caller_ownership() {
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(workspace_handle("workspace-1", "caller-1", "lease-1")));
    let manager = manager_with(&fake);

    manager.create(create_request("caller-1")).unwrap();

    let wrong_caller = manager
        .resolve(
            WorkspaceId("workspace-1".to_owned()),
            CallerId("caller-2".to_owned()),
        )
        .unwrap_err();
    assert!(matches!(
        wrong_caller,
        WorkspaceManagerError::CallerMismatch { workspace_id, .. }
            if workspace_id == WorkspaceId("workspace-1".to_owned())
    ));

    let handler = manager
        .resolve(
            WorkspaceId("workspace-1".to_owned()),
            CallerId("caller-1".to_owned()),
        )
        .unwrap();
    assert_eq!(handler.workspace_id, WorkspaceId("workspace-1".to_owned()));
}

#[test]
fn workspace_manager_create_rolls_back_raw_workspace_when_insert_fails() {
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(workspace_handle("workspace-1", "caller-1", "lease-1")));
    fake.push_create_result(Ok(workspace_handle("workspace-1", "caller-1", "lease-2")));
    let manager = manager_with(&fake);

    manager.create(create_request("caller-1")).unwrap();
    let error = manager.create(create_request("caller-1")).unwrap_err();

    assert!(matches!(
        error,
        WorkspaceManagerError::DuplicateWorkspaceId { workspace_id }
            if workspace_id == WorkspaceId("workspace-1".to_owned())
    ));
    assert_eq!(
        fake.destroy_calls(),
        vec![WorkspaceId("workspace-1".to_owned())]
    );
    assert!(manager
        .resolve(
            WorkspaceId("workspace-1".to_owned()),
            CallerId("caller-1".to_owned()),
        )
        .is_ok());
}

#[test]
fn workspace_manager_destroy_failure_retains_session() {
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(workspace_handle("workspace-1", "caller-1", "lease-1")));
    fake.push_destroy_result(Err(WorkspaceError::Setup {
        step: "destroy failed".to_owned(),
    }));
    let manager = manager_with(&fake);
    let handler = manager.create(create_request("caller-1")).unwrap();

    let error = manager
        .destroy(handler, DestroyWorkspaceRequest::default())
        .unwrap_err();

    assert!(matches!(
        error,
        WorkspaceManagerError::Workspace(WorkspaceError::Setup { .. })
    ));
    assert!(manager
        .resolve(
            WorkspaceId("workspace-1".to_owned()),
            CallerId("caller-1".to_owned()),
        )
        .is_ok());
}

#[test]
fn workspace_manager_successful_destroy_removes_session() {
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(workspace_handle("workspace-1", "caller-1", "lease-1")));
    let manager = manager_with(&fake);
    let handler = manager.create(create_request("caller-1")).unwrap();

    manager
        .destroy(handler, DestroyWorkspaceRequest::default())
        .unwrap();

    let missing = manager
        .resolve(
            WorkspaceId("workspace-1".to_owned()),
            CallerId("caller-1".to_owned()),
        )
        .unwrap_err();
    assert!(matches!(missing, WorkspaceManagerError::NotFound { .. }));
}
