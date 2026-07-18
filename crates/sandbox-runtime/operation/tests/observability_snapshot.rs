mod support;

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use sandbox_observability_telemetry::Observer;
use sandbox_runtime::command::ExecCommandInput;
use sandbox_runtime::layerstack::LayerStackService;
use sandbox_runtime::workspace_session::FinalizationState;
use sandbox_runtime::{CommandOperationService, SandboxRuntimeOperations, WorkloadCgroupLimits};
use sandbox_runtime_workspace::DestroyWorkspaceRequest;
use sandbox_runtime_workspace::{NetworkProfile, WorkspaceSessionId};

use support::{
    build_services, build_services_with_launch_driver_and_workload_cgroup, create_request,
    workspace_handle, FakeLaunchDriver, FakeWorkspaceService, TestServices,
};

#[test]
fn observability_snapshot_copies_active_workspace_fields(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fake = Arc::new(FakeWorkspaceService::new());
    let services = build_services(Arc::clone(&fake));
    let workspace_session_id = create_session(
        &fake,
        &services,
        "workspace-session",
        PathBuf::from("/workspace/session"),
        NetworkProfile::Isolated,
    );
    let operations = operations_for(&services)?;

    let snapshot = operations.observability_snapshot();

    assert!(snapshot.partial_errors.is_empty());
    assert_eq!(snapshot.workspaces.len(), 1);
    let workspace = &snapshot.workspaces[0];
    assert_eq!(workspace.workspace_id, workspace_session_id);
    assert_eq!(workspace.holder_pid, i32::try_from(std::process::id())?);
    assert_eq!(workspace.network, NetworkProfile::Isolated);
    assert_eq!(workspace.finalization_state, FinalizationState::Active);
    assert_eq!(
        workspace.workspace_root,
        PathBuf::from("/workspace/session")
    );
    assert!(workspace.upperdir.is_some());
    assert!(workspace.workdir.is_some());
    assert_eq!(workspace.namespace_fd_count, Some(4));
    assert_eq!(workspace.base_root_hash.as_deref(), Some("root"));
    assert_eq!(workspace.layer_count, Some(1));
    assert_eq!(workspace.cgroup_path, None);
    assert_eq!(workspace.applied_cgroup_limits, None);
    assert!(snapshot.active_namespace_executions.is_empty());
    assert_eq!(snapshot.ownership.namespace_fd_count, Some(0));
    assert_eq!(snapshot.ownership.control_fd_count, Some(0));
    assert_eq!(snapshot.ownership.active_scratch_directories, Some(0));
    assert_eq!(snapshot.ownership.persisted_workspace_handles, Some(0));
    assert_eq!(snapshot.ownership.exited_unreaped_holders, Some(0));

    let handler = services
        .workspace
        .resolve_session(workspace_session_id.clone())?;
    services
        .workspace
        .destroy_session(handler, DestroyWorkspaceRequest::default())?;
    assert!(operations.observability_snapshot().workspaces.is_empty());
    Ok(())
}

#[test]
fn observability_snapshot_exposes_applied_workspace_cgroup_profile(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fake = Arc::new(FakeWorkspaceService::new());
    let cgroup_root = temp_root().join("cgroup-root");
    let limits = WorkloadCgroupLimits {
        nano_cpus: 750_000_000,
        memory_high_bytes: 96 * 1024 * 1024,
        memory_max_bytes: 128 * 1024 * 1024,
        pids_max: 48,
    };
    let services = build_services_with_launch_driver_and_workload_cgroup(
        Arc::clone(&fake),
        Arc::new(FakeLaunchDriver::new()),
        cgroup_root.clone(),
        limits,
    );
    let workspace_session_id = create_session(
        &fake,
        &services,
        "profiled-session",
        PathBuf::from("/workspace/session"),
        NetworkProfile::Shared,
    );
    let operations = operations_for(&services)?;

    let allocated = operations.observability_snapshot();
    assert_eq!(
        allocated.workspaces[0].cgroup_path.as_deref(),
        Some(cgroup_root.join("workspace-profiled-session").as_path())
    );
    assert_eq!(allocated.workspaces[0].applied_cgroup_limits, Some(limits));

    services.command.exec_command(ExecCommandInput {
        workspace_session_id: Some(workspace_session_id),
        cmd: "printf ok".to_owned(),
        timeout_ms: None,
        yield_time_ms: Some(0),
    })?;

    let after_admission = operations.observability_snapshot();
    let workspace = &after_admission.workspaces[0];
    assert_eq!(
        workspace.cgroup_path.as_deref(),
        Some(cgroup_root.join("workspace-profiled-session").as_path())
    );
    assert_eq!(workspace.applied_cgroup_limits, Some(limits));
    Ok(())
}

#[test]
fn observability_snapshot_reports_active_command_namespace_execution(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fake = Arc::new(FakeWorkspaceService::new());
    let services = build_services(Arc::clone(&fake));
    let workspace_session_id = create_session(
        &fake,
        &services,
        "workspace-session",
        PathBuf::from("/workspace/session"),
        NetworkProfile::Shared,
    );
    let command_yield = services.command.exec_command(ExecCommandInput {
        workspace_session_id: Some(workspace_session_id.clone()),
        cmd: "printf ok".to_owned(),
        timeout_ms: None,
        yield_time_ms: Some(0),
    })?;
    let command_session_id = command_yield
        .command_session_id
        .expect("running command has a command id");
    let operations = operations_for(&services)?;

    let snapshot = operations.observability_snapshot();

    assert_eq!(snapshot.active_namespace_executions.len(), 1);
    let namespace_execution = &snapshot.active_namespace_executions[0];
    assert_eq!(
        namespace_execution.namespace_execution_id,
        command_session_id
    );
    assert_eq!(
        namespace_execution.workspace_session_id,
        workspace_session_id
    );
    assert_eq!(namespace_execution.operation_name, "exec_command");
    assert_eq!(namespace_execution.command.as_deref(), Some("printf ok"));
    Ok(())
}

/// The runtime now depends on `sandbox-observability-telemetry` (it carries the
/// span/event emit seams), so the old "operation excludes telemetry"
/// assertion is intentionally gone. What must still hold is that the runtime
/// never pulls a storage engine: SQLite stays out. The leaf-boundary
/// invariant (obs must not depend on runtime/daemon/manager) is owned by the obs
/// crate's own `dependency_guard.rs`.
#[test]
fn runtime_never_pulls_sqlite_storage() {
    let manifest = include_str!("../Cargo.toml");
    assert!(!manifest.contains(concat!("rusq", "lite")));
}

fn create_session(
    fake: &Arc<FakeWorkspaceService>,
    services: &TestServices,
    workspace_session_id: &str,
    workspace_root: PathBuf,
    network: NetworkProfile,
) -> WorkspaceSessionId {
    fake.push_create_result(Ok(workspace_handle(
        workspace_session_id,
        "lease-1",
        workspace_root,
        network,
    )));
    services
        .workspace
        .create_workspace_session(create_request())
        .expect("session create succeeds")
        .workspace_session_id
}

fn operations_for(
    services: &TestServices,
) -> Result<SandboxRuntimeOperations, Box<dyn std::error::Error + Send + Sync>> {
    Ok(SandboxRuntimeOperations::new(
        Arc::<CommandOperationService>::clone(&services.command),
        Arc::clone(&services.workspace),
        layerstack_service()?,
        support::test_file_service(),
    ))
}

fn layerstack_service() -> Result<Arc<LayerStackService>, Box<dyn std::error::Error + Send + Sync>>
{
    let base = temp_root();
    let root = base.join("layer-stack");
    let workspace = base.join("workspace");
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&workspace)?;
    sandbox_runtime_layerstack::build_workspace_base(&root, &workspace, false)?;
    Ok(Arc::new(LayerStackService::new(
        root,
        base.join("scratch"),
        sandbox_runtime::LayerstackRuntimeConfig::default(),
        Observer::disabled(),
        support::test_file_service(),
    )?))
}

fn temp_root() -> PathBuf {
    static NEXT_TEST: AtomicU64 = AtomicU64::new(0);
    std::env::temp_dir().join(format!(
        "sandbox-runtime-observability-{}-{}",
        std::process::id(),
        NEXT_TEST.fetch_add(1, Ordering::Relaxed)
    ))
}
