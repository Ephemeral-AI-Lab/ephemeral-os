use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use sandbox_manager::{
    ManagerError, ManagerServices, SandboxDaemonClient, SandboxDaemonEndpoint,
    SandboxDaemonInstaller, SandboxId, SandboxRecord, SandboxRuntime, SandboxState, SandboxStore,
};
use sandbox_protocol::{
    ArgKind, OperationCatalog, OperationExecutionSpace, OperationFamily, OperationScope,
    OperationSpec, Request, Response,
};
use serde_json::{json, Value};

static TEST_RUNTIME_SPEC: OperationSpec = OperationSpec {
    name: "runtime_test_operation",
    family: OperationFamily::Health,
    summary: "Test runtime operation.",
    args: &[],
    cli: None,
};

static TEST_RUNTIME_SPECS: &[&OperationSpec] = &[&TEST_RUNTIME_SPEC];

#[derive(Default)]
struct FakeRuntime {
    created: Mutex<Vec<String>>,
    destroyed: Mutex<Vec<String>>,
}

impl SandboxRuntime for FakeRuntime {
    fn create_sandbox(&self, id: &SandboxId) -> Result<(), ManagerError> {
        self.created
            .lock()
            .expect("created lock")
            .push(id.as_str().to_owned());
        Ok(())
    }

    fn destroy_sandbox(&self, record: &SandboxRecord) -> Result<(), ManagerError> {
        self.destroyed
            .lock()
            .expect("destroyed lock")
            .push(record.id.as_str().to_owned());
        Ok(())
    }
}

#[derive(Default)]
struct FakeInstaller {
    started: Mutex<Vec<String>>,
    stopped: Mutex<Vec<String>>,
}

impl SandboxDaemonInstaller for FakeInstaller {
    fn start_daemon(&self, record: &SandboxRecord) -> Result<SandboxDaemonEndpoint, ManagerError> {
        self.started
            .lock()
            .expect("started lock")
            .push(record.id.as_str().to_owned());
        Ok(SandboxDaemonEndpoint::new(
            PathBuf::from(format!("/tmp/{}.sock", record.id.as_str())),
            Some("token".to_owned()),
        ))
    }

    fn stop_daemon(&self, record: &SandboxRecord) -> Result<(), ManagerError> {
        self.stopped
            .lock()
            .expect("stopped lock")
            .push(record.id.as_str().to_owned());
        Ok(())
    }
}

#[derive(Default)]
struct FakeClient {
    described: Mutex<Vec<PathBuf>>,
}

impl SandboxDaemonClient for FakeClient {
    fn describe_operations(
        &self,
        endpoint: &SandboxDaemonEndpoint,
    ) -> Result<OperationCatalog, ManagerError> {
        self.described
            .lock()
            .expect("described lock")
            .push(endpoint.socket_path.clone());
        Ok(OperationCatalog::new(
            OperationExecutionSpace::Runtime,
            TEST_RUNTIME_SPECS,
        ))
    }

    fn invoke(
        &self,
        _endpoint: &SandboxDaemonEndpoint,
        _request: sandbox_protocol::Request,
    ) -> Result<Response, ManagerError> {
        Ok(Response::ok(json!({"forwarded": true})))
    }
}

fn services() -> (
    ManagerServices,
    Arc<FakeRuntime>,
    Arc<FakeInstaller>,
    Arc<FakeClient>,
) {
    let store = Arc::new(SandboxStore::new());
    let runtime = Arc::new(FakeRuntime::default());
    let installer = Arc::new(FakeInstaller::default());
    let client = Arc::new(FakeClient::default());
    let services = ManagerServices::new(
        Arc::clone(&store),
        runtime.clone(),
        installer.clone(),
        client.clone(),
    );
    (services, runtime, installer, client)
}

fn dispatch(services: &ManagerServices, op: &str, args: Value) -> Value {
    let request = Request::new(op, "req-1", OperationScope::System, args);
    sandbox_manager::dispatch_operation(services, &request).into_json_value()
}

fn id(value: &str) -> SandboxId {
    SandboxId::new(value).expect("valid sandbox id")
}

#[test]
fn operation_catalog_contains_only_manager_operations() {
    let catalog = sandbox_manager::operation_catalog();
    let names = catalog
        .operations
        .iter()
        .map(|spec| spec.name)
        .collect::<Vec<_>>();

    assert_eq!(
        catalog.operation_execution_space,
        OperationExecutionSpace::Manager
    );
    assert_eq!(
        names,
        [
            "create_sandbox",
            "destroy_sandbox",
            "list_sandboxes",
            "inspect_sandbox",
            "start_sandbox_daemon",
            "stop_sandbox_daemon",
            "describe_manager_operations",
            "describe_daemon_operations",
        ]
    );
    assert!(catalog.operations.iter().all(|spec| !matches!(
        spec.name,
        "exec_command"
            | "write_command_stdin"
            | "poll_command"
            | "read_command_lines"
            | "cancel_command"
    )));
    assert!(catalog.operations.iter().any(|spec| spec
        .args
        .iter()
        .any(|arg| arg.name == "sandbox_id" && arg.kind == ArgKind::String)));
    assert!(catalog.operations.iter().all(|spec| {
        spec.cli
            .map(|cli| {
                cli.examples
                    .iter()
                    .all(|example| example.starts_with("sandbox-cli manager "))
            })
            .unwrap_or(true)
    }));
}

#[test]
fn describe_manager_operations_serializes_cli_metadata() {
    let (services, _runtime, _installer, _client) = services();

    let catalog = dispatch(&services, "describe_manager_operations", json!({}));

    assert_eq!(catalog["operation_execution_space"], "manager");
    assert_eq!(catalog["operations"][0]["name"], "create_sandbox");
    assert_eq!(
        catalog["operations"][0]["args"][0]["cli"]["flag"],
        "--sandbox-id"
    );
    assert_eq!(
        catalog["operations"][0]["args"][0]["cli"]["positional"],
        Value::Null
    );
    assert_eq!(
        catalog["operations"][0]["cli"]["usage"],
        "sandbox-cli manager create_sandbox --sandbox-id ID"
    );
    assert_eq!(
        catalog["operations"][0]["cli"]["examples"][0],
        "sandbox-cli manager create_sandbox --sandbox-id sbox-1"
    );
}

#[test]
fn create_list_inspect_destroy_sandbox_with_fake_runtime() {
    let (services, runtime, _installer, _client) = services();

    let created = dispatch(&services, "create_sandbox", json!({"sandbox_id": "sbox-1"}));
    assert_eq!(created["id"], "sbox-1");
    assert_eq!(created["state"], "ready");

    let listed = dispatch(&services, "list_sandboxes", json!({}));
    assert_eq!(listed["sandboxes"][0]["id"], "sbox-1");

    let inspected = dispatch(
        &services,
        "inspect_sandbox",
        json!({"sandbox_id": "sbox-1"}),
    );
    assert_eq!(inspected["id"], "sbox-1");

    let destroyed = dispatch(
        &services,
        "destroy_sandbox",
        json!({"sandbox_id": "sbox-1"}),
    );
    assert_eq!(destroyed["state"], "stopped");

    let listed = dispatch(&services, "list_sandboxes", json!({}));
    assert_eq!(
        listed["sandboxes"]
            .as_array()
            .expect("sandboxes array")
            .len(),
        0
    );
    assert_eq!(
        runtime.created.lock().expect("created lock").as_slice(),
        ["sbox-1"]
    );
    assert_eq!(
        runtime.destroyed.lock().expect("destroyed lock").as_slice(),
        ["sbox-1"]
    );
}

#[test]
fn start_stop_daemon_updates_endpoint_with_fake_installer() {
    let (services, _runtime, installer, _client) = services();
    let _ = dispatch(&services, "create_sandbox", json!({"sandbox_id": "sbox-1"}));

    let started = dispatch(
        &services,
        "start_sandbox_daemon",
        json!({"sandbox_id": "sbox-1"}),
    );
    assert_eq!(started["daemon"]["socket_path"], "/tmp/sbox-1.sock");
    assert_eq!(started["daemon"]["auth_token_configured"], true);

    let stopped = dispatch(
        &services,
        "stop_sandbox_daemon",
        json!({"sandbox_id": "sbox-1"}),
    );
    assert!(stopped["daemon"].is_null());
    assert_eq!(
        installer.started.lock().expect("started lock").as_slice(),
        ["sbox-1"]
    );
    assert_eq!(
        installer.stopped.lock().expect("stopped lock").as_slice(),
        ["sbox-1"]
    );
}

#[test]
fn describe_daemon_operations_uses_daemon_client_trait() {
    let (services, _runtime, _installer, client) = services();
    let _ = dispatch(&services, "create_sandbox", json!({"sandbox_id": "sbox-1"}));
    let _ = dispatch(
        &services,
        "start_sandbox_daemon",
        json!({"sandbox_id": "sbox-1"}),
    );

    let catalog = dispatch(
        &services,
        "describe_daemon_operations",
        json!({"sandbox_id": "sbox-1"}),
    );
    assert_eq!(catalog["operation_execution_space"], "runtime");
    assert_eq!(catalog["operations"][0]["name"], "runtime_test_operation");
    assert!(catalog["operations"]
        .as_array()
        .expect("operations array")
        .iter()
        .all(|spec| !matches!(
            spec["name"].as_str(),
            Some("create_sandbox" | "list_sandboxes" | "destroy_sandbox")
        )));
    assert_eq!(
        client.described.lock().expect("described lock").as_slice(),
        [PathBuf::from("/tmp/sbox-1.sock")]
    );
}

#[test]
fn store_duplicate_and_missing_sandbox_error_cases() {
    let store = SandboxStore::new();
    store
        .insert(SandboxRecord::new(id("sbox-1"), SandboxState::Ready))
        .expect("insert sandbox");

    let duplicate = store
        .create(id("sbox-1"))
        .expect_err("duplicate should fail");
    assert!(matches!(duplicate, ManagerError::DuplicateSandbox { .. }));

    let missing = store
        .inspect(&id("missing"))
        .expect_err("missing should fail");
    assert!(matches!(missing, ManagerError::MissingSandbox { .. }));
}
