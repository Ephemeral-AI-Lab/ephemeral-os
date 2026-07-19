use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use sandbox_runtime::workspace_session::{
    FinalizationState, FinalizePolicy, HolderExitDisposition, WorkspaceSessionError,
    WorkspaceSessionHandler, WorkspaceSessionService,
};
use sandbox_runtime_workspace::{
    LayerStackSnapshotRef, LeaseId, NetworkProfile, WorkspaceError, WorkspaceHandle,
    WorkspaceSessionId,
};

mod support;
use support::FakeWorkspaceService;

const RECOVERY_CHILD_ROOT_ENV: &str = "EOS_TEST_RECOVERY_GENERATION_CHILD_ROOT";

fn manager_with_recovery_root(
    fake: &Arc<FakeWorkspaceService>,
) -> (WorkspaceSessionService, PathBuf) {
    let layerstack =
        support::observed_layerstack_service(sandbox_observability_telemetry::Observer::disabled());
    let recovery_root = layerstack
        .layer_stack_root()
        .parent()
        .expect("test layer stack has a storage parent")
        .join("storage")
        .join("workspace_recovery");
    let manager = WorkspaceSessionService::new(
        support::fake_workspace_runtime(Arc::clone(fake)),
        layerstack,
        sandbox_observability_telemetry::Observer::disabled(),
    );
    (manager, recovery_root)
}

fn manager_at_storage_root(
    fake: &Arc<FakeWorkspaceService>,
    storage_root: &Path,
) -> (WorkspaceSessionService, PathBuf) {
    let layer_stack_root = storage_root.join("layer-stack");
    if !layer_stack_root.exists() {
        let workspace = storage_root.join("workspace");
        fs::create_dir_all(&workspace).expect("create fixed recovery workspace");
        sandbox_runtime_layerstack::build_workspace_base(&layer_stack_root, &workspace, false)
            .expect("build fixed recovery layer stack");
    }
    let layerstack = Arc::new(
        sandbox_runtime::LayerStackService::new(
            layer_stack_root,
            storage_root.join(format!("scratch-{}", std::process::id())),
            sandbox_runtime::LayerstackRuntimeConfig::default(),
            sandbox_observability_telemetry::Observer::disabled(),
            support::test_file_service(),
        )
        .expect("open fixed recovery layer stack"),
    );
    let recovery_root = storage_root.join("storage").join("workspace_recovery");
    let manager = WorkspaceSessionService::new(
        support::fake_workspace_runtime(Arc::clone(fake)),
        layerstack,
        sandbox_observability_telemetry::Observer::disabled(),
    );
    (manager, recovery_root)
}

fn recovery_handle(
    workspace_session_id: &str,
    lease_id: &str,
    upperdir: PathBuf,
) -> WorkspaceHandle {
    let workdir = upperdir
        .parent()
        .expect("recovery source has a parent")
        .join(format!("work-{lease_id}"));
    WorkspaceHandle::holder_backed_for_test(
        WorkspaceSessionId(workspace_session_id.to_owned()),
        PathBuf::from("/workspace"),
        NetworkProfile::Shared,
        LayerStackSnapshotRef {
            lease_id: LeaseId(lease_id.to_owned()),
            manifest_version: 1,
            root_hash: "root".to_owned(),
            manifest: support::test_manifest(),
            layer_paths: vec![PathBuf::from("/lower/one")],
        },
        upperdir,
        workdir,
    )
}

fn seed_recovery_source(handle: &WorkspaceHandle, content: &[u8]) {
    let upperdir = handle.entry().expect("handle has recovery source").upperdir;
    fs::create_dir_all(&upperdir).expect("create recovery source");
    fs::write(upperdir.join("generation"), content).expect("seed recovery source");
}

fn create_with_handle(
    manager: &WorkspaceSessionService,
    fake: &Arc<FakeWorkspaceService>,
    handle: WorkspaceHandle,
) -> WorkspaceSessionHandler {
    fake.push_create_result(Ok(handle));
    manager
        .create_workspace_session(support::create_request_with_policy(
            FinalizePolicy::PublishThenDestroy,
        ))
        .expect("session creates")
}

fn recovery_artifacts(recovery_root: &Path) -> Vec<PathBuf> {
    let mut artifacts = fs::read_dir(recovery_root)
        .expect("recovery root exists")
        .map(|entry| entry.expect("recovery entry is readable").path())
        .collect::<Vec<_>>();
    artifacts.sort();
    artifacts
}

fn recovery_required_artifact(
    outcomes: &[sandbox_runtime::workspace_session::HolderExitOutcome],
) -> PathBuf {
    match outcomes {
        [outcome] => match &outcome.disposition {
            HolderExitDisposition::RecoveryRequired { artifact } => artifact.clone(),
            disposition => panic!("unexpected holder-exit disposition: {disposition:?}"),
        },
        outcomes => panic!("unexpected holder-exit outcomes: {outcomes:?}"),
    }
}

fn create_first_generation_in_child(storage_root: &Path) {
    let fake = Arc::new(FakeWorkspaceService::new());
    let (manager, recovery_root) = manager_at_storage_root(&fake, storage_root);
    let workspace_id = "reused-recovery-id";
    let handle = recovery_handle(
        workspace_id,
        "lease-first-generation",
        storage_root.join("upper-first-generation"),
    );
    seed_recovery_source(&handle, b"first");
    let first = create_with_handle(&manager, &fake, handle);
    fake.push_destroy_result(Err(WorkspaceError::Cleanup {
        workspace_session_id: workspace_id.to_owned(),
        failures: vec!["Mounts: injected first-attempt failure".to_owned()],
    }));
    fake.mark_holder_exited(&first.handle, "signal:9");

    let first_attempt = manager.reconcile_holder_exits();

    assert!(matches!(
        first_attempt.as_slice(),
        [outcome]
            if matches!(
                outcome.disposition,
                HolderExitDisposition::RetryableCleanupFailure { .. }
            )
    ));
    let first_artifact = recovery_artifacts(&recovery_root)
        .into_iter()
        .next()
        .expect("first recovery artifact commits before teardown");

    let first_retry = manager.reconcile_holder_exits();

    assert_eq!(
        recovery_required_artifact(&first_retry),
        first_artifact,
        "retrying one generation validates and reuses its committed artifact"
    );
    assert_eq!(recovery_artifacts(&recovery_root), vec![first_artifact]);
}

#[test]
fn recovery_artifact_is_bounded_durable_sanitized_and_idempotent() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let (manager, recovery_root) = manager_with_recovery_root(&fake);
    let source = recovery_root
        .parent()
        .expect("recovery root has a storage parent")
        .parent()
        .expect("storage has a fixture parent")
        .join("bounded-upper");
    let handle = recovery_handle("workspace/unsafe", "lease-bounded-recovery", source.clone());
    fs::create_dir_all(source.join("nested")).expect("create recovery source tree");
    fs::write(source.join("nested/large"), vec![b'x'; 2 * 1024 * 1024])
        .expect("seed oversized recovery content");
    let failed = create_with_handle(&manager, &fake, handle);
    fake.push_destroy_result(Err(WorkspaceError::Cleanup {
        workspace_session_id: failed.workspace_session_id.0.clone(),
        failures: vec!["Mounts: injected first-attempt failure".to_owned()],
    }));
    fake.mark_holder_exited(&failed.handle, "signal:9");

    let first = manager.reconcile_holder_exits();

    assert!(matches!(
        first.as_slice(),
        [outcome]
            if matches!(
                outcome.disposition,
                HolderExitDisposition::RetryableCleanupFailure { .. }
            )
    ));
    let first_artifact = recovery_artifacts(&recovery_root)
        .into_iter()
        .next()
        .expect("bounded recovery artifact commits before teardown retry");
    let retry = manager.reconcile_holder_exits();
    let retry_artifact = recovery_required_artifact(&retry);

    assert_eq!(first_artifact, retry_artifact);
    assert!(first_artifact.join("manifest.json").is_file());
    let manifest: serde_json::Value = serde_json::from_slice(
        &fs::read(first_artifact.join("manifest.json")).expect("read recovery manifest"),
    )
    .expect("recovery manifest is valid JSON");
    let content_max = manifest["content_max_bytes"]
        .as_u64()
        .expect("manifest records the content bound");
    assert_eq!(
        fs::metadata(first_artifact.join("files/nested/large"))
            .expect("bounded recovery file exists")
            .len(),
        content_max
    );
    assert!(content_max < 1024 * 1024);
    assert_eq!(manifest["artifact_max_bytes"], 1024 * 1024);
    assert_eq!(manifest["truncated"], true);
    assert_eq!(manifest["finalization_state"], "finalization_failed");
    assert!(!recovery_root.join("workspace/unsafe").exists());
}

#[test]
fn reused_public_id_uses_generation_scoped_recovery_artifacts() {
    if let Some(storage_root) = std::env::var_os(RECOVERY_CHILD_ROOT_ENV) {
        create_first_generation_in_child(&PathBuf::from(storage_root));
        return;
    }

    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock is after Unix epoch")
        .as_nanos();
    let storage_root = std::env::temp_dir().join(format!(
        "operation-service-recovery-generation-{}-{unique}",
        std::process::id()
    ));
    let child = Command::new(std::env::current_exe().expect("resolve integration test binary"))
        .arg("--exact")
        .arg("reused_public_id_uses_generation_scoped_recovery_artifacts")
        .arg("--nocapture")
        .arg("--test-threads=1")
        .env(RECOVERY_CHILD_ROOT_ENV, &storage_root)
        .output()
        .expect("run first holder generation in a distinct process");
    assert!(
        child.status.success(),
        "first holder generation failed\nstdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&child.stdout),
        String::from_utf8_lossy(&child.stderr)
    );

    let recovery_root = storage_root.join("storage").join("workspace_recovery");
    let first_artifact = recovery_artifacts(&recovery_root)
        .into_iter()
        .next()
        .expect("child generation committed a recovery artifact");
    let fake = Arc::new(FakeWorkspaceService::new());
    let (manager, reopened_recovery_root) = manager_at_storage_root(&fake, &storage_root);
    assert_eq!(recovery_root, reopened_recovery_root);
    let workspace_id = "reused-recovery-id";
    let second_handle = recovery_handle(
        workspace_id,
        "lease-second-generation",
        storage_root.join("upper-second-generation"),
    );
    seed_recovery_source(&second_handle, b"second");
    let second = create_with_handle(&manager, &fake, second_handle);
    fake.mark_holder_exited(&second.handle, "signal:9");

    let second_attempt = manager.reconcile_holder_exits();
    let second_artifact = recovery_required_artifact(&second_attempt);

    assert_ne!(first_artifact, second_artifact);
    assert_eq!(
        fs::read(second_artifact.join("files/generation"))
            .expect("second recovery content is readable"),
        b"second"
    );
    let first_manifest: serde_json::Value = serde_json::from_slice(
        &fs::read(first_artifact.join("manifest.json")).expect("first recovery manifest"),
    )
    .expect("first manifest is valid JSON");
    let second_manifest: serde_json::Value = serde_json::from_slice(
        &fs::read(second_artifact.join("manifest.json")).expect("second recovery manifest"),
    )
    .expect("second manifest is valid JSON");
    assert_eq!(first_manifest["workspace_session_id"], workspace_id);
    assert_eq!(second_manifest["workspace_session_id"], workspace_id);
    assert_ne!(
        first_manifest["holder_identity_sha256"],
        second_manifest["holder_identity_sha256"]
    );
    assert_ne!(
        first_manifest["source_upperdir_sha256"],
        second_manifest["source_upperdir_sha256"]
    );
    assert_eq!(recovery_artifacts(&recovery_root).len(), 2);
    drop(manager);
    fs::remove_dir_all(&storage_root).expect("remove run-scoped recovery fixture");
}

#[test]
fn retry_rejects_tampered_recovery_manifest_before_raw_teardown() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let (manager, recovery_root) = manager_with_recovery_root(&fake);
    let workspace_id = WorkspaceSessionId("tampered-recovery-owner".to_owned());
    let handle = support::workspace_handle(
        &workspace_id.0,
        "lease-tampered-recovery",
        PathBuf::from("/workspace"),
        NetworkProfile::Shared,
    );
    seed_recovery_source(&handle, b"recoverable");
    let failed = create_with_handle(&manager, &fake, handle);
    fake.push_destroy_result(Err(WorkspaceError::Cleanup {
        workspace_session_id: workspace_id.0.clone(),
        failures: vec!["Mounts: injected first-attempt failure".to_owned()],
    }));
    fake.mark_holder_exited(&failed.handle, "signal:9");

    let first_attempt = manager.reconcile_holder_exits();

    assert!(matches!(
        first_attempt.as_slice(),
        [outcome]
            if matches!(
                outcome.disposition,
                HolderExitDisposition::RetryableCleanupFailure { .. }
            )
    ));
    assert_eq!(fake.destroy_calls(), vec![workspace_id.clone()]);
    let artifact = recovery_artifacts(&recovery_root)
        .into_iter()
        .next()
        .expect("recovery artifact committed before failed teardown");
    let manifest_path = artifact.join("manifest.json");
    let mut manifest: serde_json::Value =
        serde_json::from_slice(&fs::read(&manifest_path).expect("recovery manifest is readable"))
            .expect("recovery manifest is valid JSON");
    let original_generation = manifest["holder_generation"]
        .as_u64()
        .expect("manifest records holder generation");
    manifest["holder_generation"] = serde_json::Value::from(original_generation + 1);
    fs::write(
        &manifest_path,
        serde_json::to_vec_pretty(&manifest).expect("tampered manifest encodes"),
    )
    .expect("tamper recovery owner");

    let retry = manager.reconcile_holder_exits();

    assert!(matches!(
        retry.as_slice(),
        [outcome]
            if matches!(
                &outcome.disposition,
                HolderExitDisposition::RetryableCleanupFailure { diagnostic }
                    if diagnostic.contains("wrong generation, holder identity, or source")
            )
    ));
    assert_eq!(
        fake.destroy_calls(),
        vec![workspace_id.clone()],
        "manifest ownership is validated before another raw teardown call"
    );
    assert!(matches!(
        manager.resolve_session(workspace_id),
        Err(WorkspaceSessionError::HolderExited {
            cleanup_state: FinalizationState::FinalizeFailed,
            ..
        })
    ));
}
