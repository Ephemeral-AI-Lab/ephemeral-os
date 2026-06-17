use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use operation_service::command::CommandOperationService;
use operation_service::workspace_manager::WorkspaceManagerService;
use operation_service::workspace_remount::{WorkspaceRemountOptions, WorkspaceRemountService};
use operation_service::OperationServices;
use workspace::{
    BaseRevision, CallerId, CaptureChangesRequest, CapturedWorkspaceChanges,
    CreateWorkspaceRequest, DestroyWorkspaceRequest, DestroyWorkspaceResult, LatestSnapshotRequest,
    LayerStackSnapshotRef, LeaseId, NetworkMode, ReadonlySnapshotHandle, RemountWorkspaceRequest,
    RemountWorkspaceResult, WorkspaceError, WorkspaceHandle, WorkspaceService,
};

pub struct TestServices {
    pub workspace: Arc<WorkspaceManagerService>,
    pub command: Arc<CommandOperationService>,
    pub services: OperationServices,
}

pub struct FakeWorkspaceService {
    create_results: Mutex<VecDeque<Result<WorkspaceHandle, WorkspaceError>>>,
    destroy_results: Mutex<VecDeque<Result<DestroyWorkspaceResult, WorkspaceError>>>,
    create_requests: Mutex<Vec<CreateWorkspaceRequest>>,
    destroy_calls: Mutex<Vec<WorkspaceId>>,
}

use workspace::WorkspaceId;

impl FakeWorkspaceService {
    pub fn new() -> Self {
        Self {
            create_results: Mutex::new(VecDeque::new()),
            destroy_results: Mutex::new(VecDeque::new()),
            create_requests: Mutex::new(Vec::new()),
            destroy_calls: Mutex::new(Vec::new()),
        }
    }

    pub fn push_create_result(&self, result: Result<WorkspaceHandle, WorkspaceError>) {
        self.create_results
            .lock()
            .expect("test operation succeeds")
            .push_back(result);
    }

    pub fn create_requests(&self) -> Vec<CreateWorkspaceRequest> {
        self.create_requests
            .lock()
            .expect("test operation succeeds")
            .clone()
    }

    pub fn destroy_calls(&self) -> Vec<WorkspaceId> {
        self.destroy_calls
            .lock()
            .expect("test operation succeeds")
            .clone()
    }
}

impl WorkspaceService for FakeWorkspaceService {
    fn create_workspace(
        &self,
        request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceHandle, WorkspaceError> {
        self.create_requests
            .lock()
            .expect("test operation succeeds")
            .push(request);
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

pub fn build_services(fake: Arc<FakeWorkspaceService>) -> TestServices {
    let workspace = Arc::new(WorkspaceManagerService::new(fake));
    let command = Arc::new(CommandOperationService::new(
        Arc::clone(&workspace),
        command::CommandConfig::default(),
    ));
    let remount = Arc::new(WorkspaceRemountService::new(
        Arc::clone(&workspace),
        Arc::clone(&command),
        WorkspaceRemountOptions::default(),
    ));
    let services = OperationServices::new(Arc::clone(&workspace), Arc::clone(&command), remount);

    TestServices {
        workspace,
        command,
        services,
    }
}

pub fn create_request(caller_id: &str, workspace_root: PathBuf) -> CreateWorkspaceRequest {
    CreateWorkspaceRequest {
        caller_id: CallerId(caller_id.to_owned()),
        workspace_root,
        layer_stack_root: PathBuf::from("/layers"),
        network: NetworkMode::Host,
    }
}

pub fn assert_private_host_create_request(
    request: &CreateWorkspaceRequest,
    caller_id: &str,
    workspace_root: &PathBuf,
) {
    assert_eq!(request.caller_id, CallerId(caller_id.to_owned()));
    assert_eq!(&request.workspace_root, workspace_root);
    assert_eq!(&request.layer_stack_root, workspace_root);
    assert_eq!(request.network, NetworkMode::Host);
}

pub fn workspace_handle(
    workspace_id: &str,
    caller_id: &str,
    lease_id: &str,
    workspace_root: PathBuf,
    network: NetworkMode,
) -> WorkspaceHandle {
    let snapshot = LayerStackSnapshotRef {
        lease_id: LeaseId(lease_id.to_owned()),
        manifest_version: 1,
        root_hash: "root".to_owned(),
        layer_paths: vec![PathBuf::from("/lower/one")],
    };
    WorkspaceHandle {
        id: WorkspaceId(workspace_id.to_owned()),
        owner: CallerId(caller_id.to_owned()),
        workspace_root,
        network,
        base_revision: BaseRevision {
            version: 1,
            root_hash: "root".to_owned(),
            layer_count: 1,
        },
        snapshot,
    }
}

pub fn destroy_result(handle: &WorkspaceHandle) -> DestroyWorkspaceResult {
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
