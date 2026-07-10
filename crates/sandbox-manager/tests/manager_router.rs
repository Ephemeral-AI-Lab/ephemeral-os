use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use sandbox_manager::{
    CreateSandboxRequest, CreateSandboxResult, ManagerError, ManagerServices, SandboxDaemonClient,
    SandboxDaemonEndpoint, SandboxDaemonInstaller, SandboxId, SandboxManagerRouter, SandboxRecord,
    SandboxRuntime, SandboxState, SandboxStore, StartedDaemon,
};
use sandbox_operation_contract::{error, OperationRequest, OperationResponse, OperationScope};
use serde_json::{json, Value};

struct FakeRuntime;

impl SandboxRuntime for FakeRuntime {
    fn create_sandbox(
        &self,
        _request: &CreateSandboxRequest,
    ) -> Result<CreateSandboxResult, ManagerError> {
        Ok(CreateSandboxResult {
            id: sandbox_id("container-1"),
        })
    }

    fn destroy_sandbox(&self, _record: &SandboxRecord) -> Result<(), ManagerError> {
        Ok(())
    }
}

struct FakeInstaller;

impl SandboxDaemonInstaller for FakeInstaller {
    fn install_daemon(&self, _record: &SandboxRecord) -> Result<(), ManagerError> {
        Ok(())
    }

    fn start_daemon(&self, record: &SandboxRecord) -> Result<StartedDaemon, ManagerError> {
        Ok(StartedDaemon {
            daemon: SandboxDaemonEndpoint::new(
                "127.0.0.1",
                7000,
                format!("token-{}", record.id.as_str()),
            ),
            daemon_http: None,
        })
    }

    fn stop_daemon(&self, _record: &SandboxRecord) -> Result<(), ManagerError> {
        Ok(())
    }

    fn check_daemon(
        &self,
        _record: &SandboxRecord,
        _endpoint: &SandboxDaemonEndpoint,
    ) -> Result<(), ManagerError> {
        Ok(())
    }
}

#[derive(Default)]
struct RecordingDaemonClient {
    invocations: Mutex<Vec<(u16, String, OperationScope)>>,
}

impl SandboxDaemonClient for RecordingDaemonClient {
    fn invoke(
        &self,
        endpoint: &SandboxDaemonEndpoint,
        request: OperationRequest,
        _timeout_override: Option<Duration>,
    ) -> Result<OperationResponse, ManagerError> {
        self.invocations.lock().expect("invocations lock").push((
            endpoint.port,
            request.op.clone(),
            request.scope.clone(),
        ));
        Ok(OperationResponse::ok(json!({"forwarded": true})))
    }
}

fn services() -> (
    Arc<ManagerServices>,
    Arc<SandboxStore>,
    Arc<RecordingDaemonClient>,
) {
    let store = Arc::new(SandboxStore::new());
    let runtime = Arc::new(FakeRuntime);
    let installer = Arc::new(FakeInstaller);
    let daemon_client = Arc::new(RecordingDaemonClient::default());
    let services = Arc::new(ManagerServices::new(
        Arc::clone(&store),
        runtime,
        installer,
        daemon_client.clone(),
    ));
    (services, store, daemon_client)
}

fn router(services: Arc<ManagerServices>) -> SandboxManagerRouter {
    SandboxManagerRouter::new(services)
}

fn request(op: &str, scope: OperationScope, args: Value) -> OperationRequest {
    OperationRequest::new(op, "req-1", scope, args)
}

fn sandbox_id(value: &str) -> SandboxId {
    SandboxId::new(value).expect("valid sandbox id")
}

fn ready_record(value: &str, daemon: Option<SandboxDaemonEndpoint>) -> SandboxRecord {
    SandboxRecord {
        id: sandbox_id(value),
        workspace_root: PathBuf::from("/testbed"),
        state: SandboxState::Ready,
        daemon,
        daemon_http: None,
        shared_base: None,
    }
}

#[tokio::test]
async fn manager_router_dispatches_system_manager_operation_locally() {
    let (services, _store, _daemon_client) = services();
    let router = router(services);

    let response = router
        .dispatch_request(request("list_sandboxes", OperationScope::System, json!({})))
        .await
        .into_json_value();

    assert_eq!(response["sandboxes"], json!([]));
}

#[tokio::test]
async fn manager_router_dispatches_hidden_observability_snapshot_locally() {
    let (services, store, daemon_client) = services();
    store
        .insert(ready_record(
            "sbox-1",
            Some(SandboxDaemonEndpoint::new(
                "127.0.0.1",
                7000,
                "token-sbox-1",
            )),
        ))
        .expect("insert sandbox");
    let router = router(services);

    let response = router
        .dispatch_request(request("snapshot", OperationScope::System, json!({})))
        .await
        .into_json_value();

    assert_eq!(response["sandboxes"][0]["sandbox_id"], "sbox-1");
    let invocations = daemon_client.invocations.lock().expect("invocations lock");
    assert_eq!(invocations.len(), 1);
    assert_eq!(invocations[0].1, "get_observability");
    assert_eq!(invocations[0].2, OperationScope::sandbox("sbox-1"));
}

#[tokio::test]
async fn manager_router_rejects_manager_operation_with_sandbox_scope() {
    let (services, _store, _daemon_client) = services();
    let router = router(services);

    let response = router
        .dispatch_request(request(
            "list_sandboxes",
            OperationScope::sandbox("sbox-1"),
            json!({}),
        ))
        .await
        .into_json_value();

    assert_eq!(response["error"]["kind"], error::INVALID_REQUEST);
}

#[tokio::test]
async fn manager_router_unknown_system_operation_returns_unknown_op() {
    let (services, _store, _daemon_client) = services();
    let router = router(services);

    let response = router
        .dispatch_request(request("exec_command", OperationScope::System, json!({})))
        .await
        .into_json_value();

    assert_eq!(response["error"]["kind"], "unknown_op");
}

#[tokio::test]
async fn manager_router_forwards_sandbox_scoped_unknown_to_daemon_client() {
    let (services, store, daemon_client) = services();
    store
        .insert(ready_record(
            "sbox-1",
            Some(SandboxDaemonEndpoint::new(
                "127.0.0.1",
                7000,
                "token-sbox-1",
            )),
        ))
        .expect("insert sandbox");
    let router = router(services);

    let response = router
        .dispatch_request(request(
            "exec_command",
            OperationScope::sandbox("sbox-1"),
            json!({"cmd": "pwd"}),
        ))
        .await
        .into_json_value();

    assert_eq!(response["forwarded"], true);
    let invocations = daemon_client.invocations.lock().expect("invocations lock");
    assert_eq!(invocations.len(), 1);
    assert_eq!(invocations[0].0, 7000);
    assert_eq!(invocations[0].1, "exec_command");
    assert_eq!(invocations[0].2, OperationScope::sandbox("sbox-1"));
}

#[tokio::test]
async fn manager_router_rejects_sandbox_scope_when_sandbox_missing() {
    let (services, _store, _daemon_client) = services();
    let router = router(services);

    let response = router
        .dispatch_request(request(
            "exec_command",
            OperationScope::sandbox("missing"),
            json!({}),
        ))
        .await
        .into_json_value();

    assert_eq!(response["error"]["kind"], error::INVALID_REQUEST);
}

#[tokio::test]
async fn manager_router_rejects_sandbox_scope_when_daemon_unavailable() {
    let (services, store, _daemon_client) = services();
    store
        .insert(ready_record("sbox-1", None))
        .expect("insert sandbox");
    let router = router(services);

    let response = router
        .dispatch_request(request(
            "exec_command",
            OperationScope::sandbox("sbox-1"),
            json!({}),
        ))
        .await
        .into_json_value();

    assert_eq!(response["error"]["kind"], error::INVALID_REQUEST);
}
