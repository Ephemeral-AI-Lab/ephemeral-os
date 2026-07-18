mod support;

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use sandbox_observability_telemetry::Observer;
use sandbox_operation_contract::{OperationRequest, OperationScope};
use sandbox_runtime::{LayerStackService, SandboxRuntimeOperations};
use sandbox_runtime_layerstack::{LayerChange, LayerPath, Manifest};
use sandbox_runtime_workspace::{
    CapturedWorkspaceChanges, LayerStackSnapshotRef, LeaseId, NetworkProfile, WorkspaceHandle,
    WorkspaceSessionId,
};
use serde_json::{json, Value};

struct TestRoot(PathBuf);

impl TestRoot {
    fn new() -> Result<Self, std::io::Error> {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "workspace-session-publish-security-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path)?;
        Ok(Self(path))
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TestRoot {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[test]
fn publish_route_preparation_rejection_hides_internal_layer_message(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let root = TestRoot::new()?;
    let layer_stack_root = root.path().join("layer-stack");
    let workspace_root = root.path().join("workspace");
    std::fs::create_dir_all(workspace_root.join("target"))?;
    std::fs::write(workspace_root.join("target/secret.txt"), "base\n")?;
    sandbox_runtime_layerstack::build_workspace_base(&layer_stack_root, &workspace_root, false)?;
    let manifest = sandbox_runtime_layerstack::LayerStack::open(layer_stack_root.clone())?
        .read_active_manifest()?;
    let internal_layer = manifest
        .layers
        .first()
        .expect("workspace base has one layer")
        .clone();
    let file = support::test_file_service();
    let layerstack = Arc::new(LayerStackService::new(
        layer_stack_root.clone(),
        root.path().join("scratch"),
        sandbox_runtime::LayerstackRuntimeConfig::default(),
        Observer::disabled(),
        Arc::clone(&file),
    )?);
    std::fs::remove_dir_all(layer_stack_root.join(&internal_layer.path))?;

    let handle = session_handle(
        "ws-path-security",
        &workspace_root,
        &layer_stack_root,
        manifest.clone(),
    );
    let fake = Arc::new(support::FakeWorkspaceService::new());
    fake.push_create_result(Ok(handle.clone()));
    fake.push_capture_result(Ok(CapturedWorkspaceChanges {
        workspace_session_id: handle.id.clone(),
        base_revision: handle.base_revision(),
        base_manifest: manifest,
        changed_path_kinds: Default::default(),
        protected_drops: Vec::new(),
        stats: None,
        metadata_path_count: 1,
        changed_paths: vec!["target".to_owned()],
        changes: vec![LayerChange::OpaqueDir {
            path: LayerPath::parse("target")?,
        }],
    }));
    let services = support::build_services_with_launch_driver_and_layerstack(
        Arc::clone(&fake),
        Arc::new(support::FakeLaunchDriver::new()),
        Arc::clone(&layerstack),
    );
    let operations = SandboxRuntimeOperations::new(
        services.command,
        Arc::clone(&services.workspace),
        layerstack,
        file,
    );
    operations
        .workspace_session
        .create_workspace_session(support::create_request())?;

    let response = sandbox_runtime::dispatch_operation(
        &operations,
        &OperationRequest::new(
            "publish_workspace_session",
            "req-path-security",
            OperationScope::sandbox("sandbox-path-security"),
            json!({"workspace_session_id": "ws-path-security"}),
        ),
    )
    .into_json_value();

    assert_eq!(response["error"]["kind"], "operation_failed");
    assert_eq!(response["error"]["details"]["stage"], "publish");
    assert_eq!(
        response["error"]["details"]["publish_rejection"],
        json!({
            "path": null,
            "reason": "route_preparation_failed",
            "source_conflict": null,
            "protected_drop": null,
            "message": null,
        })
    );
    assert_public_response_hides(
        &response,
        [
            internal_layer.layer_id.as_str(),
            internal_layer.path.as_str(),
            layer_stack_root.to_string_lossy().as_ref(),
            workspace_root.to_string_lossy().as_ref(),
        ],
    );
    assert!(operations
        .workspace_session
        .resolve_session(WorkspaceSessionId("ws-path-security".to_owned()))
        .is_ok());
    Ok(())
}

fn session_handle(
    workspace_session_id: &str,
    workspace_root: &Path,
    layer_stack_root: &Path,
    manifest: Manifest,
) -> WorkspaceHandle {
    let snapshot = LayerStackSnapshotRef {
        lease_id: LeaseId(format!("lease-{workspace_session_id}")),
        manifest_version: manifest.version,
        root_hash: sandbox_runtime_layerstack::manifest_root_hash(&manifest),
        layer_paths: manifest
            .layers
            .iter()
            .map(|layer| layer_stack_root.join(&layer.path))
            .collect(),
        manifest,
    };
    WorkspaceHandle::holder_backed_for_test(
        WorkspaceSessionId(workspace_session_id.to_owned()),
        workspace_root.to_path_buf(),
        NetworkProfile::Shared,
        snapshot,
        workspace_root.join("upper"),
        workspace_root.join("work"),
    )
}

fn assert_public_response_hides<'a>(response: &Value, values: impl IntoIterator<Item = &'a str>) {
    let encoded = serde_json::to_string(response).expect("response serializes");
    for value in values {
        assert!(
            !encoded.contains(value),
            "public response leaked internal value {value:?}: {encoded}"
        );
    }
}
