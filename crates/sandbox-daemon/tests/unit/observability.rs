use std::error::Error;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use crate::observability::DaemonObservability;
use crate::server::{SandboxDaemonServer, ServerConfig};
use sandbox_observability::{ObservabilityPaths, ObservabilityStore};
use sandbox_protocol::{CliOperationScope, Request};
use sandbox_runtime::command::CommandSessionId;
use sandbox_runtime::WorkspaceSessionId;
use sandbox_runtime::{
    CommandRuntimeConfig, Rfc1918Egress, RuntimeExecutionSnapshot,
    RuntimeObservabilitySnapshot, RuntimeWorkspaceSnapshot, SandboxRuntimeConfig,
    SandboxRuntimeOperations, WorkspaceProfile, WorkspaceResourceCaps, WorkspaceRuntimeConfig,
};
use serde_json::json;

type TestResult<T = ()> = Result<T, Box<dyn Error + Send + Sync>>;

#[test]
fn observability_collection_writes_phase2_live_snapshot() -> TestResult {
    let root = test_root("collects-phase2");
    let config = server_config(&root, Some("sandbox-1"));
    let observability =
        DaemonObservability::from_config(&config).expect("sandbox id enables observability");
    let snapshot = runtime_snapshot(root.join("missing-upperdir"));

    observability.collect_runtime_snapshot_for_test(&config, snapshot)?;

    let paths = ObservabilityPaths::from_socket_path(&config.socket_path)?;
    let store = ObservabilityStore::open(&paths)?;
    let sandbox = store
        .sandbox_snapshot_for_test("sandbox-1")?
        .expect("sandbox snapshot written");
    assert_eq!(sandbox.state, "ready");
    assert_eq!(
        sandbox.socket_path.as_deref(),
        Some(config.socket_path.to_string_lossy().as_ref())
    );

    let workspaces = store.workspace_snapshots_for_test("sandbox-1")?;
    assert_eq!(workspaces.len(), 1);
    assert_eq!(workspaces[0].workspace_id, "workspace-1");
    assert_eq!(workspaces[0].state, "active");
    assert_eq!(workspaces[0].remount_state.as_deref(), Some("active"));

    let executions = store.execution_snapshots_for_test("sandbox-1")?;
    assert_eq!(executions.len(), 1);
    assert_eq!(executions[0].execution_id, "cmd_1");
    assert_eq!(executions[0].execution_kind, "command");
    assert_eq!(executions[0].workspace_id, "workspace-1");

    let samples = store.resource_samples_for_test("sandbox-1")?;
    assert_eq!(samples.len(), 2);
    let global = samples
        .iter()
        .find(|sample| sample.workspace_id.is_none())
        .expect("sandbox-global sample written");
    assert!(!global.cgroup_available);
    assert_eq!(
        global.cgroup_error.as_deref(),
        Some("cgroup path unavailable")
    );
    let workspace = samples
        .iter()
        .find(|sample| sample.workspace_id.as_deref() == Some("workspace-1"))
        .expect("workspace sample written");
    assert!(!workspace.cgroup_available);
    assert!(workspace.disk_read_error_count.unwrap_or_default() > 0);
    assert!(workspace.disk_first_error_path.is_some());
    Ok(())
}

#[test]
fn observability_is_disabled_when_sandbox_id_is_missing() {
    let root = test_root("missing-sandbox-id");
    let config = server_config(&root, None);

    assert!(DaemonObservability::from_config(&config).is_none());
}

#[tokio::test]
async fn observability_write_errors_do_not_alter_operation_responses() -> TestResult {
    let root = test_root("write-error-response");
    let config = server_config(&root, Some("sandbox-1"));
    let observability =
        DaemonObservability::from_config(&config).expect("sandbox id enables observability");
    let invalid_snapshot = RuntimeObservabilitySnapshot {
        workspaces: vec![RuntimeWorkspaceSnapshot {
            workspace_id: WorkspaceSessionId("workspace-id-that-is-too-long".repeat(20)),
            remount_state: "active".to_owned(),
            profile: WorkspaceProfile::HostCompatible,
            workspace_root: root.join("workspace"),
            upperdir: None,
            workdir: None,
            namespace_fd_count: None,
            base_manifest_version: None,
            base_root_hash: None,
            layer_count: None,
        }],
        active_executions: Vec::new(),
        partial_errors: Vec::new(),
    };
    assert!(observability
        .collect_runtime_snapshot_for_test(&config, invalid_snapshot)
        .is_err());

    let server = SandboxDaemonServer::new(config, Arc::new(runtime_operations(&root)?));
    let response = server
        .dispatch_bytes(
            serde_json::to_vec(&Request::new(
                "unknown_operation",
                "req-1",
                CliOperationScope::sandbox("sandbox-1"),
                json!({}),
            ))?,
            false,
        )
        .await;

    assert_eq!(response["error"]["kind"], "unknown_op");
    assert_eq!(response["error"]["message"], "unknown operation");
    Ok(())
}

fn runtime_snapshot(missing_upperdir: PathBuf) -> RuntimeObservabilitySnapshot {
    RuntimeObservabilitySnapshot {
        workspaces: vec![RuntimeWorkspaceSnapshot {
            workspace_id: WorkspaceSessionId("workspace-1".to_owned()),
            remount_state: "active".to_owned(),
            profile: WorkspaceProfile::HostCompatible,
            workspace_root: PathBuf::from("/workspace/workspace-1"),
            upperdir: Some(missing_upperdir),
            workdir: Some(PathBuf::from("/workspace/workspace-1/work")),
            namespace_fd_count: Some(3),
            base_manifest_version: Some(1),
            base_root_hash: Some("root".to_owned()),
            layer_count: Some(1),
        }],
        active_executions: vec![RuntimeExecutionSnapshot {
            execution_id: "cmd_1".to_owned(),
            execution_kind: "command".to_owned(),
            operation: Some("exec_command".to_owned()),
            command_session_id: Some(CommandSessionId("cmd_1".to_owned())),
            workspace_id: WorkspaceSessionId("workspace-1".to_owned()),
            command: Some("printf ok".to_owned()),
            lifecycle_state: "running".to_owned(),
            finalization_state: "not_started".to_owned(),
            workspace_ownership: "existing_session".to_owned(),
            started_at_unix_ms: None,
            wall_time_ms: Some(10.0),
            transcript_path: Some(PathBuf::from("/tmp/transcript.log")),
            process_group_id: Some(1234),
        }],
        partial_errors: Vec::new(),
    }
}

fn runtime_operations(root: &Path) -> TestResult<SandboxRuntimeOperations> {
    let layer_stack_root = root.join("layer-stack");
    let workspace_root = root.join("workspace-root");
    let workspace_base = root.join("workspace-base");
    std::fs::create_dir_all(&workspace_base)?;
    sandbox_runtime_layerstack::build_workspace_base(&layer_stack_root, &workspace_base, false)?;

    Ok(SandboxRuntimeOperations::from_config(SandboxRuntimeConfig {
        workspace: WorkspaceRuntimeConfig {
            workspace_root,
            layer_stack_root,
            scratch_root: root.join("workspace-scratch"),
            caps: WorkspaceResourceCaps {
                upperdir_bytes: 1024 * 1024,
                memavail_fraction: 0.5,
                setup_timeout_s: 1.0,
                exit_grace_s: 1.0,
                rfc1918_egress: Rfc1918Egress::Deny,
            },
        },
        command: CommandRuntimeConfig {
            scratch_root: root.join("command-scratch"),
        },
    }))
}

fn server_config(root: &Path, sandbox_id: Option<&str>) -> ServerConfig {
    ServerConfig {
        socket_path: root.join("runtime.sock"),
        pid_path: root.join("runtime.pid"),
        tcp_host: None,
        tcp_port: None,
        auth_token: None,
        sandbox_id: sandbox_id.map(str::to_owned),
    }
}

fn test_root(label: &str) -> PathBuf {
    static NEXT_TEST: AtomicU64 = AtomicU64::new(0);
    let root = std::env::temp_dir().join(format!(
        "sandbox-daemon-observability-{label}-{}-{}",
        std::process::id(),
        NEXT_TEST.fetch_add(1, Ordering::Relaxed)
    ));
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(&root).expect("create test root");
    root
}
