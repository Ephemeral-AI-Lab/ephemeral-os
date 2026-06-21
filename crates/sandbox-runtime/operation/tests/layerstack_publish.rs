mod support;

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use sandbox_protocol::{OperationScope, Request};
use sandbox_runtime::command::{
    CommandPublishStatus, CommandServiceError, CommandStatus, ExecCommandInput, PollCommandInput,
};
use sandbox_runtime::SandboxRuntimeOperations;
use sandbox_runtime_command::process::KillReason;
use sandbox_runtime_command::yield_wait_loop::WaitOutcome;
use sandbox_runtime_workspace::{
    BaseRevision, CapturedWorkspaceChanges, ChangedPathKind, LayerStackSnapshotRef, LeaseId,
    ProtectedPathDrop, ProtectedPathDropReason, RemountWorkspaceResult, WorkspaceError,
    WorkspaceHandle, WorkspaceProfile, WorkspaceSessionId,
};
use serde_json::json;

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
        WorkspaceProfile::HostCompatible,
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

fn capture_with_protected_drops(
    handle: &WorkspaceHandle,
    changes: Vec<sandbox_runtime_layerstack::LayerChange>,
    protected_drops: Vec<ProtectedPathDrop>,
) -> CapturedWorkspaceChanges {
    let mut captured = capture(handle, changes);
    captured.metadata_path_count = captured
        .metadata_path_count
        .saturating_add(protected_drops.len());
    captured.protected_drops = protected_drops;
    captured
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
    fake.push_remount_result(Ok(RemountWorkspaceResult {
        handle: handle.clone(),
    }));
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
        fake.remount_calls(),
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

    let command_session_id = match error {
        CommandServiceError::CommandFinalizationFailed {
            command_session_id,
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
            command_session_id
        }
        other => panic!("unexpected error: {other:?}"),
    };
    let polled = env.command.poll_command(PollCommandInput {
        command_session_id,
        last_n_lines: None,
    })?;
    assert_eq!(polled.status, CommandStatus::Failed);
    let publish = polled
        .finalized
        .and_then(|metadata| metadata.publish)
        .expect("failed completion retains publish metadata");
    assert_eq!(publish.status, CommandPublishStatus::Rejected);
    assert_eq!(
        read_text(&fixture, "README.md")?,
        Some("active\n".to_owned())
    );
    Ok(())
}

#[test]
fn cancelled_command_finalization_does_not_publish_or_capture(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("cancelled")?;
    std::fs::write(fixture.workspace.join("README.md"), "base\n")?;
    let base = fixture.build_base()?;
    let handle = workspace_handle(base, &fixture.root);
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(handle));
    let mut cancelled = success_exit("cancelled\n");
    cancelled.kill = Some(KillReason::Cancelled);
    let launch_driver = Arc::new(FakeLaunchDriver::new());
    launch_driver.push_outcome(WaitOutcome::Completed(cancelled));
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

    assert_eq!(output.status, CommandStatus::Failed);
    let publish = output
        .finalized
        .and_then(|metadata| metadata.publish)
        .expect("publish metadata is present");
    assert_eq!(publish.status, CommandPublishStatus::Skipped);
    assert!(fake.capture_calls().is_empty());
    assert!(fake.remount_calls().is_empty());
    assert_eq!(read_text(&fixture, "README.md")?, Some("base\n".to_owned()));
    Ok(())
}

#[test]
fn publish_remount_failure_refreshes_snapshot_and_blocks_followup_commands(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("remount-failure")?;
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
    fake.push_remount_result(Err(WorkspaceError::Setup {
        step: "remount failed".to_owned(),
    }));
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
        .exec_command(exec_input(workspace_session_id.clone()))
        .expect_err("remount failure fails finalization");

    let command_session_id = match error {
        CommandServiceError::CommandFinalizationFailed {
            command_session_id, ..
        } => command_session_id,
        other => panic!("unexpected error: {other:?}"),
    };
    assert_eq!(
        read_text(&fixture, "README.md")?,
        Some("command\n".to_owned())
    );
    let active_manifest = sandbox_runtime_layerstack::LayerStack::open(fixture.root.clone())?
        .read_active_manifest()?;
    let resolved = env
        .workspace
        .resolve_session(workspace_session_id.clone())?;
    assert_eq!(
        resolved.handle.snapshot.root_hash,
        sandbox_runtime_layerstack::manifest_root_hash(&active_manifest)
    );
    assert_eq!(resolved.handle.snapshot.manifest, active_manifest);
    assert!(env.workspace.is_remount_blocked(&workspace_session_id));

    let polled = env.command.poll_command(PollCommandInput {
        command_session_id,
        last_n_lines: None,
    })?;
    assert_eq!(polled.status, CommandStatus::Failed);
    let blocked = env
        .command
        .exec_command(exec_input(workspace_session_id))
        .expect_err("blocked remount rejects followup commands");
    assert!(matches!(
        blocked,
        CommandServiceError::WorkspaceSessionRemountBlocked { .. }
    ));
    Ok(())
}

#[test]
fn public_command_response_includes_structured_publish_rejection_details(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("public-structured-reject")?;
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
    let layerstack = Arc::new(fixture.service()?);
    let env = build_services_with_launch_driver_and_layerstack(
        Arc::clone(&fake),
        launch_driver,
        Arc::clone(&layerstack),
    );
    let workspace_session_id = env
        .workspace
        .create_workspace_session(create_request())?
        .workspace_session_id;
    let operations = SandboxRuntimeOperations::new(Arc::clone(&env.command), layerstack);

    let response = sandbox_runtime::dispatch_operation(
        &operations,
        &Request::new(
            "exec_command",
            "req-1",
            OperationScope::system(),
            json!({
                "workspace_session_id": workspace_session_id.0,
                "cmd": "printf ok",
                "yield_time_ms": 0,
            }),
        ),
    )
    .into_json_value();

    assert_eq!(
        response["error"]["details"]["finalized"]["publish"]["status"],
        "rejected"
    );
    assert_eq!(
        response["error"]["details"]["finalized"]["publish"]["rejection"]["reason"],
        "source_conflict"
    );
    assert_eq!(
        response["error"]["details"]["finalized"]["publish"]["rejection"]["source_conflict"]
            ["path"],
        "README.md"
    );
    assert_eq!(
        response["error"]["details"]["finalized"]["publish"]["rejection"]["source_conflict"]
            ["expected"]["kind"],
        "file"
    );
    assert_eq!(
        response["error"]["details"]["finalized"]["publish"]["rejection"]["source_conflict"]
            ["actual"]["kind"],
        "file"
    );
    Ok(())
}

#[test]
fn command_scratch_protected_drop_rejects_publish_with_structured_metadata(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("scratch-protected-drop")?;
    std::fs::write(fixture.workspace.join("README.md"), "base\n")?;
    let base = fixture.build_base()?;
    let handle = workspace_handle(base, &fixture.root);
    let fake = Arc::new(FakeWorkspaceService::new());
    fake.push_create_result(Ok(handle.clone()));
    fake.push_capture_result(Ok(capture_with_protected_drops(
        &handle,
        Vec::new(),
        vec![ProtectedPathDrop {
            path: ".command-scratch/cmd-1".to_owned(),
            reason: ProtectedPathDropReason::CommandScratchPath,
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
        .expect_err("protected drop rejects publish");

    match error {
        CommandServiceError::CommandFinalizationFailed {
            finalized: Some(finalized),
            ..
        } => {
            let publish = finalized.publish.expect("publish metadata is retained");
            assert_eq!(publish.status, CommandPublishStatus::Rejected);
            let rejection = publish.rejection.expect("publish rejection is retained");
            let protected_drop = rejection
                .protected_drop
                .expect("protected drop is retained");
            assert_eq!(
                protected_drop.reason,
                sandbox_runtime_layerstack::LayerProtectedDropReason::CommandScratchPath
            );
        }
        other => panic!("unexpected error: {other:?}"),
    }
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

#[test]
fn layerstack_service_empty_changes_return_no_op_revision(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("service-empty-no-op")?;
    std::fs::write(fixture.workspace.join("README.md"), "base\n")?;
    let base = fixture.build_base()?;
    let service = fixture.service()?;
    let revision = sandbox_runtime::layerstack::LayerStackRevision {
        manifest_version: base.version,
        root_hash: sandbox_runtime_layerstack::manifest_root_hash(&base),
        layer_count: base.layers.len(),
    };

    let result = service.publish_changes(sandbox_runtime::layerstack::PublishChangesRequest {
        expected_base: revision.clone(),
        base_manifest: base.clone(),
        protected_drops: Vec::new(),
        changes: Vec::new(),
    })?;

    assert!(result.no_op);
    assert_eq!(result.revision, revision);
    assert_eq!(result.manifest, base);
    assert_eq!(result.route_summary.source_count, 0);
    assert_eq!(result.route_summary.ignored_count, 0);
    Ok(())
}

#[test]
fn layerstack_service_ignored_only_publish_preserves_route_summary(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("service-ignored-only")?;
    std::fs::write(fixture.workspace.join(".gitignore"), "out.log\n")?;
    let base = fixture.build_base()?;
    let service = fixture.service()?;
    let revision = sandbox_runtime::layerstack::LayerStackRevision {
        manifest_version: base.version,
        root_hash: sandbox_runtime_layerstack::manifest_root_hash(&base),
        layer_count: base.layers.len(),
    };

    let result = service.publish_changes(sandbox_runtime::layerstack::PublishChangesRequest {
        expected_base: revision,
        base_manifest: base,
        protected_drops: Vec::new(),
        changes: vec![sandbox_runtime_layerstack::LayerChange::Write {
            path: lp("out.log"),
            content: b"ignored\n".to_vec(),
        }],
    })?;

    assert!(!result.no_op);
    assert_eq!(result.route_summary.source_count, 0);
    assert_eq!(result.route_summary.ignored_count, 1);
    assert_eq!(
        read_text(&fixture, "out.log")?,
        Some("ignored\n".to_owned())
    );
    Ok(())
}

#[test]
fn layerstack_service_squash_reports_no_op_for_unsquashable_stack(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("service-squash-no-op")?;
    std::fs::write(fixture.workspace.join("README.md"), "base\n")?;
    let base = fixture.build_base()?;
    let service = fixture.service()?;

    let result = service.squash()?;

    assert!(!result.squashed);
    assert_eq!(result.revision, None);
    assert!(result.layer_paths.is_empty());
    let active = sandbox_runtime_layerstack::LayerStack::open(fixture.root.clone())?
        .read_active_manifest()?;
    assert_eq!(active, base);
    Ok(())
}
