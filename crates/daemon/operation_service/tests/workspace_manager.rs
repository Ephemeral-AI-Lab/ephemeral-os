use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use operation_service::workspace_manager::{WorkspaceManagerError, WorkspaceManagerService};
use workspace::{
    BaseRevision, CallerId, CaptureChangesRequest, CapturedWorkspaceChanges,
    CreateWorkspaceRequest, DestroyWorkspaceRequest, DestroyWorkspaceResult, LatestSnapshotRequest,
    LayerStackSnapshotRef, LeaseId, NetworkMode, ReadonlySnapshotHandle, RemountWorkspaceRequest,
    RemountWorkspaceResult, WorkspaceError, WorkspaceHandle, WorkspaceId, WorkspaceService,
};

struct FakeWorkspaceService {
    create_results: Mutex<VecDeque<Result<WorkspaceHandle, WorkspaceError>>>,
    capture_results: Mutex<VecDeque<Result<CapturedWorkspaceChanges, WorkspaceError>>>,
    remount_results: Mutex<VecDeque<Result<RemountWorkspaceResult, WorkspaceError>>>,
    destroy_results: Mutex<VecDeque<Result<DestroyWorkspaceResult, WorkspaceError>>>,
    capture_calls: Mutex<Vec<WorkspaceId>>,
    remount_calls: Mutex<Vec<WorkspaceId>>,
    destroy_calls: Mutex<Vec<WorkspaceId>>,
}

impl FakeWorkspaceService {
    fn new() -> Self {
        Self {
            create_results: Mutex::new(VecDeque::new()),
            capture_results: Mutex::new(VecDeque::new()),
            remount_results: Mutex::new(VecDeque::new()),
            destroy_results: Mutex::new(VecDeque::new()),
            capture_calls: Mutex::new(Vec::new()),
            remount_calls: Mutex::new(Vec::new()),
            destroy_calls: Mutex::new(Vec::new()),
        }
    }

    fn push_create_result(&self, result: Result<WorkspaceHandle, WorkspaceError>) {
        self.create_results
            .lock()
            .expect("test operation succeeds")
            .push_back(result);
    }

    fn push_capture_result(&self, result: Result<CapturedWorkspaceChanges, WorkspaceError>) {
        self.capture_results
            .lock()
            .expect("test operation succeeds")
            .push_back(result);
    }

    fn push_remount_result(&self, result: Result<RemountWorkspaceResult, WorkspaceError>) {
        self.remount_results
            .lock()
            .expect("test operation succeeds")
            .push_back(result);
    }

    fn push_destroy_result(&self, result: Result<DestroyWorkspaceResult, WorkspaceError>) {
        self.destroy_results
            .lock()
            .expect("test operation succeeds")
            .push_back(result);
    }

    fn capture_calls(&self) -> Vec<WorkspaceId> {
        self.capture_calls
            .lock()
            .expect("test operation succeeds")
            .clone()
    }

    fn remount_calls(&self) -> Vec<WorkspaceId> {
        self.remount_calls
            .lock()
            .expect("test operation succeeds")
            .clone()
    }

    fn destroy_calls(&self) -> Vec<WorkspaceId> {
        self.destroy_calls
            .lock()
            .expect("test operation succeeds")
            .clone()
    }
}

impl WorkspaceService for FakeWorkspaceService {
    fn create_workspace(
        &self,
        _request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceHandle, WorkspaceError> {
        self.create_results
            .lock()
            .expect("test operation succeeds")
            .pop_front()
            .unwrap_or_else(|| {
                Err(WorkspaceError::Setup {
                    step: "create result not configured".to_owned(),
                })
            })
    }

    fn capture_changes(
        &self,
        handle: &WorkspaceHandle,
        _request: CaptureChangesRequest,
    ) -> Result<CapturedWorkspaceChanges, WorkspaceError> {
        self.capture_calls
            .lock()
            .expect("test operation succeeds")
            .push(handle.id.clone());
        self.capture_results
            .lock()
            .expect("test operation succeeds")
            .pop_front()
            .unwrap_or_else(|| {
                Err(WorkspaceError::Capture {
                    message: "capture result not configured".to_owned(),
                })
            })
    }

    fn remount_workspace(
        &self,
        handle: &WorkspaceHandle,
        _request: RemountWorkspaceRequest,
    ) -> Result<RemountWorkspaceResult, WorkspaceError> {
        self.remount_calls
            .lock()
            .expect("test operation succeeds")
            .push(handle.id.clone());
        self.remount_results
            .lock()
            .expect("test operation succeeds")
            .pop_front()
            .unwrap_or_else(|| {
                Err(WorkspaceError::Setup {
                    step: "remount result not configured".to_owned(),
                })
            })
    }

    fn destroy_workspace(
        &self,
        handle: WorkspaceHandle,
        _request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceError> {
        self.destroy_calls
            .lock()
            .expect("test operation succeeds")
            .push(handle.id.clone());
        self.destroy_results
            .lock()
            .expect("test operation succeeds")
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
        caller_id: CallerId(caller_id.to_owned()),
        workspace_root: PathBuf::from("/workspace"),
        layer_stack_root: PathBuf::from("/layers"),
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
        evicted_upperdir_bytes: 0,
        lifetime_s: 0.0,
        lease_released: Some(true),
        lease_release_error: None,
        active_leases_after: 0,
    }
}

fn capture_result(
    handle: &WorkspaceHandle,
    version: i64,
    root_hash: &str,
) -> CapturedWorkspaceChanges {
    CapturedWorkspaceChanges {
        workspace_id: handle.id.clone(),
        base_revision: BaseRevision {
            version,
            root_hash: root_hash.to_owned(),
            layer_count: handle.snapshot.layer_paths.len(),
        },
        changed_paths: Vec::new(),
        changed_path_kinds: Default::default(),
        protected_drops: Vec::new(),
        stats: None,
        changes: Vec::new(),
        route_stats: layerstack::CaptureRouteStats::default(),
        metadata_path_count: 0,
        spool_dir: None,
    }
}

#[test]
fn workspace_manager_resolve_validates_caller_ownership() {
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(workspace_handle("workspace-1", "caller-1", "lease-1")));
    let manager = manager_with(&fake);

    manager
        .create(create_request("caller-1"))
        .expect("test operation succeeds");

    let wrong_caller = manager
        .resolve(
            WorkspaceId("workspace-1".to_owned()),
            CallerId("caller-2".to_owned()),
        )
        .expect_err("test operation fails");
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
        .expect("test operation succeeds");
    assert_eq!(handler.workspace_id, WorkspaceId("workspace-1".to_owned()));
}

#[test]
fn workspace_manager_create_rolls_back_raw_workspace_when_insert_fails() {
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(workspace_handle("workspace-1", "caller-1", "lease-1")));
    fake.push_create_result(Ok(workspace_handle("workspace-1", "caller-1", "lease-2")));
    let manager = manager_with(&fake);

    manager
        .create(create_request("caller-1"))
        .expect("test operation succeeds");
    let error = manager
        .create(create_request("caller-1"))
        .expect_err("test operation fails");

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
    let handler = manager
        .create(create_request("caller-1"))
        .expect("test operation succeeds");

    let error = manager
        .destroy(handler, DestroyWorkspaceRequest::default())
        .expect_err("test operation fails");

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
    let handler = manager
        .create(create_request("caller-1"))
        .expect("test operation succeeds");

    manager
        .destroy(handler, DestroyWorkspaceRequest::default())
        .expect("test operation succeeds");

    let missing = manager
        .resolve(
            WorkspaceId("workspace-1".to_owned()),
            CallerId("caller-1".to_owned()),
        )
        .expect_err("test operation fails");
    assert!(matches!(missing, WorkspaceManagerError::NotFound { .. }));
}

#[test]
fn workspace_manager_rejects_stale_handler_before_raw_capture() {
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(workspace_handle("workspace-1", "caller-1", "lease-1")));
    let manager = manager_with(&fake);
    let handler = manager
        .create(create_request("caller-1"))
        .expect("test operation succeeds");

    manager
        .destroy(handler.clone(), DestroyWorkspaceRequest::default())
        .expect("test operation succeeds");

    let error = manager
        .capture_changes(
            &handler,
            CaptureChangesRequest {
                bounds: layerstack::service::BoundedCaptureOptions {
                    materialize_payloads: false,
                    ..layerstack::service::BoundedCaptureOptions::default()
                },
                include_stats: false,
            },
        )
        .expect_err("test operation fails");

    assert!(matches!(error, WorkspaceManagerError::NotFound { .. }));
    assert!(fake.capture_calls().is_empty());
}

#[test]
fn workspace_manager_uses_canonical_handle_for_capture() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let handle = workspace_handle("workspace-1", "caller-1", "lease-1");
    fake.push_create_result(Ok(handle.clone()));
    fake.push_capture_result(Ok(capture_result(&handle, 2, "root-2")));
    let manager = manager_with(&fake);
    let mut handler = manager
        .create(create_request("caller-1"))
        .expect("test operation succeeds");
    handler.handle.id = WorkspaceId("fabricated".to_owned());

    manager
        .capture_changes(
            &handler,
            CaptureChangesRequest {
                bounds: layerstack::service::BoundedCaptureOptions {
                    materialize_payloads: false,
                    ..layerstack::service::BoundedCaptureOptions::default()
                },
                include_stats: false,
            },
        )
        .expect("test operation succeeds");

    assert_eq!(
        fake.capture_calls(),
        vec![WorkspaceId("workspace-1".to_owned())]
    );
}

#[test]
fn workspace_manager_capture_updates_handler_snapshot_consistently() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let handle = workspace_handle("workspace-1", "caller-1", "lease-1");
    fake.push_create_result(Ok(handle.clone()));
    fake.push_capture_result(Ok(capture_result(&handle, 2, "root-2")));
    let manager = manager_with(&fake);
    let handler = manager
        .create(create_request("caller-1"))
        .expect("test operation succeeds");

    manager
        .capture_changes(
            &handler,
            CaptureChangesRequest {
                bounds: layerstack::service::BoundedCaptureOptions {
                    materialize_payloads: false,
                    ..layerstack::service::BoundedCaptureOptions::default()
                },
                include_stats: false,
            },
        )
        .expect("test operation succeeds");

    let resolved = manager
        .resolve(
            WorkspaceId("workspace-1".to_owned()),
            CallerId("caller-1".to_owned()),
        )
        .expect("test operation succeeds");
    assert_eq!(resolved.snapshot.manifest_version, 2);
    assert_eq!(resolved.handle.snapshot.manifest_version, 2);
    assert_eq!(resolved.snapshot.root_hash, "root-2");
    assert_eq!(resolved.handle.snapshot.root_hash, "root-2");
}

#[test]
fn workspace_manager_rejects_remount_workspace_id_mismatch() {
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(workspace_handle("workspace-1", "caller-1", "lease-1")));
    fake.push_remount_result(Ok(RemountWorkspaceResult {
        handle: workspace_handle("workspace-2", "caller-1", "lease-2"),
    }));
    let manager = manager_with(&fake);
    let handler = manager
        .create(create_request("caller-1"))
        .expect("test operation succeeds");

    let error = manager
        .remount_workspace(
            &handler,
            RemountWorkspaceRequest {
                layer_paths: vec![PathBuf::from("/lower/two")],
            },
        )
        .expect_err("test operation fails");

    assert!(matches!(
        error,
        WorkspaceManagerError::RemountWorkspaceIdMismatch { expected, actual }
            if expected == WorkspaceId("workspace-1".to_owned())
                && actual == WorkspaceId("workspace-2".to_owned())
    ));
    assert_eq!(
        fake.remount_calls(),
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
fn workspace_manager_duplicate_destroy_does_not_call_raw_destroy_twice() {
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(workspace_handle("workspace-1", "caller-1", "lease-1")));
    let manager = manager_with(&fake);
    let handler = manager
        .create(create_request("caller-1"))
        .expect("test operation succeeds");

    manager
        .destroy(handler.clone(), DestroyWorkspaceRequest::default())
        .expect("test operation succeeds");
    let duplicate = manager
        .destroy(handler, DestroyWorkspaceRequest::default())
        .expect_err("test operation fails");

    assert!(matches!(duplicate, WorkspaceManagerError::NotFound { .. }));
    assert_eq!(
        fake.destroy_calls(),
        vec![WorkspaceId("workspace-1".to_owned())]
    );
}
