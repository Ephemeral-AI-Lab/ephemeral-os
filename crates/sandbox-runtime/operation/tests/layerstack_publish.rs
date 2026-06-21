mod support;

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use sandbox_runtime::command::{CommandPublishStatus, CommandServiceError, ExecCommandInput};
use sandbox_runtime_command::yield_wait_loop::WaitOutcome;
use sandbox_runtime_workspace::{
    BaseRevision, CapturedWorkspaceChanges, ChangedPathKind, LayerStackSnapshotRef, LeaseId,
    WorkspaceHandle, WorkspaceProfile, WorkspaceSessionId,
};

use support::{
    build_services_with_launch_driver_and_layerstack, create_request, success_exit,
    FakeLaunchDriver, FakeWorkspaceService,
};

struct PublishFixture {
    base: PathBuf,
    root: PathBuf,
    workspace: PathBuf,
}

impl PublishFixture {
    fn new(label: &str) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let base = std::env::temp_dir().join(format!(
            "operation-layerstack-publish-{label}-{}-{}",
            std::process::id(),
            NEXT_TEST.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&base);
        let root = base.join("layer-stack");
        let workspace = base.join("workspace");
        std::fs::create_dir_all(&workspace)?;
        Ok(Self {
            base,
            root,
            workspace,
        })
    }

    fn build_base(
        &self,
    ) -> Result<sandbox_runtime_layerstack::Manifest, Box<dyn std::error::Error + Send + Sync>>
    {
        sandbox_runtime_layerstack::build_workspace_base(&self.root, &self.workspace, false)?;
        let stack = sandbox_runtime_layerstack::LayerStack::open(self.root.clone())?;
        Ok(stack.read_active_manifest()?)
    }

    fn service(
        &self,
    ) -> Result<
        sandbox_runtime::layerstack::LayerStackService,
        Box<dyn std::error::Error + Send + Sync>,
    > {
        Ok(sandbox_runtime::layerstack::LayerStackService::new(
            self.root.clone(),
        )?)
    }
}

impl Drop for PublishFixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.base);
    }
}

static NEXT_TEST: AtomicU64 = AtomicU64::new(0);

fn exec_input(workspace_session_id: WorkspaceSessionId) -> ExecCommandInput {
    ExecCommandInput {
        workspace_session_id,
        cmd: "printf ok".to_owned(),
        timeout_seconds: None,
        yield_time_ms: Some(0),
    }
}

fn workspace_handle(
    manifest: sandbox_runtime_layerstack::Manifest,
    layer_stack_root: &std::path::Path,
) -> WorkspaceHandle {
    let root_hash = sandbox_runtime_layerstack::manifest_root_hash(&manifest);
    let layer_paths = manifest
        .layers
        .iter()
        .map(|layer| layer_stack_root.join(&layer.path))
        .collect::<Vec<_>>();
    let snapshot = LayerStackSnapshotRef {
        lease_id: LeaseId("lease-1".to_owned()),
        manifest_version: manifest.version,
        root_hash,
        manifest,
        layer_paths,
    };
    WorkspaceHandle::holder_backed_for_test(
        WorkspaceSessionId("workspace-session".to_owned()),
        PathBuf::from("/workspace/session"),
        WorkspaceProfile::SharedNetwork,
        snapshot,
        std::env::temp_dir().join("operation-layerstack-publish-upper"),
        std::env::temp_dir().join("operation-layerstack-publish-work"),
        None,
    )
}

fn capture(
    handle: &WorkspaceHandle,
    changes: Vec<sandbox_runtime_layerstack::LayerChange>,
) -> CapturedWorkspaceChanges {
    let changed_paths = changes
        .iter()
        .map(|change| change.path().as_str().to_owned())
        .collect::<Vec<_>>();
    let changed_path_kinds = changes
        .iter()
        .map(|change| {
            (
                change.path().as_str().to_owned(),
                ChangedPathKind::from(change),
            )
        })
        .collect::<BTreeMap<_, _>>();
    CapturedWorkspaceChanges {
        workspace_session_id: handle.id.clone(),
        base_revision: BaseRevision {
            version: handle.snapshot.manifest_version,
            root_hash: handle.snapshot.root_hash.clone(),
            layer_count: handle.snapshot.layer_paths.len(),
        },
        base_manifest: handle.snapshot.manifest.clone(),
        changed_paths,
        changed_path_kinds,
        protected_drops: Vec::new(),
        stats: None,
        metadata_path_count: changes.len(),
        changes,
    }
}

fn read_text(
    fixture: &PublishFixture,
    path: &str,
) -> Result<Option<String>, Box<dyn std::error::Error + Send + Sync>> {
    let stack = sandbox_runtime_layerstack::LayerStack::open(fixture.root.clone())?;
    let manifest = stack.read_active_manifest()?;
    let view = sandbox_runtime_layerstack::MergedView::new(fixture.root.clone());
    let (bytes, exists) = view.read_bytes(path, &manifest)?;
    if !exists {
        return Ok(None);
    }
    let bytes = bytes.expect("merged view returned bytes for existing path");
    Ok(Some(String::from_utf8(bytes).expect("test file is utf8")))
}

fn lp(path: &str) -> sandbox_runtime_layerstack::LayerPath {
    sandbox_runtime_layerstack::LayerPath::parse(path).expect("test path is valid")
}

#[test]
fn successful_command_finalization_publishes_captured_changes(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("success")?;
    std::fs::write(fixture.workspace.join("README.md"), "base\n")?;
    let base = fixture.build_base()?;
    let handle = workspace_handle(base, &fixture.root);
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(handle.clone()));
    fake.push_capture_result(Ok(capture(
        &handle,
        vec![sandbox_runtime_layerstack::LayerChange::Write {
            path: lp("README.md"),
            content: b"command\n".to_vec(),
        }],
    )));
    let launch_driver = Arc::new(FakeLaunchDriver::new());
    launch_driver.push_outcome(WaitOutcome::Completed(success_exit("done\n")));
    let env = build_services_with_launch_driver_and_layerstack(
        Arc::clone(&fake),
        launch_driver,
        Arc::new(fixture.service()?),
    );
    let workspace_session_id = env
        .workspace
        .create_workspace_session(create_request())?
        .workspace_session_id;

    let output = env.command.exec_command(exec_input(workspace_session_id))?;

    let publish = output
        .finalized
        .and_then(|metadata| metadata.publish)
        .expect("publish metadata is present");
    assert_eq!(publish.status, CommandPublishStatus::Published);
    assert_eq!(
        fake.capture_calls(),
        vec![WorkspaceSessionId("workspace-session".to_owned())]
    );
    assert_eq!(
        read_text(&fixture, "README.md")?,
        Some("command\n".to_owned())
    );
    let resolved = env
        .workspace
        .resolve_session(WorkspaceSessionId("workspace-session".to_owned()))?;
    let publish_revision = publish.revision.expect("publish revision is returned");
    assert_eq!(
        resolved.handle.snapshot.manifest_version,
        publish_revision.manifest_version
    );
    assert_eq!(
        resolved.handle.snapshot.root_hash,
        publish_revision.root_hash
    );
    assert_eq!(
        sandbox_runtime_layerstack::manifest_root_hash(&resolved.handle.snapshot.manifest),
        resolved.handle.snapshot.root_hash
    );
    Ok(())
}

#[test]
fn failed_command_finalization_does_not_publish_or_capture(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("failed")?;
    std::fs::write(fixture.workspace.join("README.md"), "base\n")?;
    let base = fixture.build_base()?;
    let handle = workspace_handle(base, &fixture.root);
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(handle));
    let mut failed = success_exit("failed\n");
    failed.status = "failed".to_owned();
    failed.exit_code = 1;
    let launch_driver = Arc::new(FakeLaunchDriver::new());
    launch_driver.push_outcome(WaitOutcome::Completed(failed));
    let env = build_services_with_launch_driver_and_layerstack(
        Arc::clone(&fake),
        launch_driver,
        Arc::new(fixture.service()?),
    );
    let workspace_session_id = env
        .workspace
        .create_workspace_session(create_request())?
        .workspace_session_id;

    let output = env.command.exec_command(exec_input(workspace_session_id))?;

    let publish = output
        .finalized
        .and_then(|metadata| metadata.publish)
        .expect("publish metadata is present");
    assert_eq!(publish.status, CommandPublishStatus::Skipped);
    assert!(fake.capture_calls().is_empty());
    assert_eq!(read_text(&fixture, "README.md")?, Some("base\n".to_owned()));
    Ok(())
}

#[test]
fn publish_conflict_marks_finalization_failed_with_structured_metadata(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("conflict")?;
    std::fs::write(fixture.workspace.join("README.md"), "base\n")?;
    let base = fixture.build_base()?;
    let handle = workspace_handle(base, &fixture.root);
    sandbox_runtime_layerstack::LayerStack::open(fixture.root.clone())?.publish_layer(&[
        sandbox_runtime_layerstack::LayerChange::Write {
            path: lp("README.md"),
            content: b"active\n".to_vec(),
        },
    ])?;
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(handle.clone()));
    fake.push_capture_result(Ok(capture(
        &handle,
        vec![sandbox_runtime_layerstack::LayerChange::Write {
            path: lp("README.md"),
            content: b"command\n".to_vec(),
        }],
    )));
    let launch_driver = Arc::new(FakeLaunchDriver::new());
    launch_driver.push_outcome(WaitOutcome::Completed(success_exit("done\n")));
    let env = build_services_with_launch_driver_and_layerstack(
        Arc::clone(&fake),
        launch_driver,
        Arc::new(fixture.service()?),
    );
    let workspace_session_id = env
        .workspace
        .create_workspace_session(create_request())?
        .workspace_session_id;

    let error = env
        .command
        .exec_command(exec_input(workspace_session_id))
        .expect_err("publish conflict fails finalization");

    match error {
        CommandServiceError::CommandFinalizationFailed {
            finalized: Some(finalized),
            ..
        } => {
            let publish = finalized.publish.expect("publish metadata is retained");
            assert_eq!(publish.status, CommandPublishStatus::Rejected);
            assert!(matches!(
                publish.rejection.as_deref(),
                Some(rejection)
                    if rejection.reason
                        == sandbox_runtime_layerstack::PublishRejectReason::SourceConflict
            ));
        }
        other => panic!("unexpected error: {other:?}"),
    }
    assert_eq!(
        read_text(&fixture, "README.md")?,
        Some("active\n".to_owned())
    );
    Ok(())
}

#[test]
fn layerstack_service_rejects_invalid_base_revision(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("invalid-base")?;
    std::fs::write(fixture.workspace.join("README.md"), "base\n")?;
    let base = fixture.build_base()?;
    let service = fixture.service()?;

    let error = service
        .publish_changes(sandbox_runtime::layerstack::PublishChangesRequest {
            expected_base: sandbox_runtime::layerstack::LayerStackRevision {
                manifest_version: base.version,
                root_hash: "not-the-base-root".to_owned(),
                layer_count: base.layers.len(),
            },
            base_manifest: base,
            protected_drops: Vec::new(),
            changes: Vec::new(),
        })
        .expect_err("invalid base metadata rejects before publish");

    assert!(matches!(
        error,
        sandbox_runtime::layerstack::LayerStackServiceError::InvalidBaseRevision { .. }
    ));
    Ok(())
}

#[test]
fn layerstack_service_preserves_structured_publish_rejection(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("structured-reject")?;
    std::fs::write(fixture.workspace.join("README.md"), "base\n")?;
    let base = fixture.build_base()?;
    let service = fixture.service()?;
    let revision = sandbox_runtime::layerstack::LayerStackRevision {
        manifest_version: base.version,
        root_hash: sandbox_runtime_layerstack::manifest_root_hash(&base),
        layer_count: base.layers.len(),
    };

    let error = service
        .publish_changes(sandbox_runtime::layerstack::PublishChangesRequest {
            expected_base: revision,
            base_manifest: base,
            protected_drops: Vec::new(),
            changes: vec![sandbox_runtime_layerstack::LayerChange::Write {
                path: lp(".git/config"),
                content: b"bad\n".to_vec(),
            }],
        })
        .expect_err("git mutation rejects publish");

    match error {
        sandbox_runtime::layerstack::LayerStackServiceError::PublishRejected { rejection } => {
            assert_eq!(
                rejection.reason,
                sandbox_runtime_layerstack::PublishRejectReason::GitMutationForbidden
            );
            assert_eq!(
                rejection.path.as_ref().map(ToString::to_string).as_deref(),
                Some(".git/config")
            );
        }
        other => panic!("unexpected error: {other:?}"),
    }
    Ok(())
}
