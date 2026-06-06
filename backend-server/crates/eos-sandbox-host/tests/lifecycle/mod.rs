#![allow(clippy::unwrap_used)]
use std::sync::Arc;

use super::*;
use crate::provider::{ProviderAdapter, RawExecResult};
use crate::registry::ProviderRegistry;
use crate::support::MockAdapter;

fn sid() -> SandboxId {
    "sb-1".parse().unwrap()
}

fn lifecycle_with(adapter: MockAdapter, artifact_dir: PathBuf) -> SandboxLifecycle {
    let registry = ProviderRegistry::new();
    registry.set_default(Arc::new(adapter));
    SandboxLifecycle::new(
        Arc::new(DaemonClient::new(Arc::new(registry))),
        artifact_dir,
    )
}

// create registers the adapter; with no project_dir the setup sequence is a
// no-op (every step guards on a non-empty workspace).
#[tokio::test]
async fn create_registers_and_setup_noops_without_workspace() {
    let lifecycle = lifecycle_with(
        MockAdapter::new().with_id("box"),
        PathBuf::from("/nonexistent"),
    );
    let info = lifecycle
        .create(&CreateSandboxSpec {
            name: "box".to_owned(),
            ..Default::default()
        })
        .await
        .unwrap();
    assert_eq!(info.id.as_str(), "box");
    assert!(lifecycle.daemon.registry().has(&info.id));
}

// ensure_running returns early when the probe is healthy (no setup).
#[tokio::test]
async fn ensure_running_healthy_returns_early() {
    let lifecycle = lifecycle_with(
        MockAdapter::new()
            .with_id("box")
            .with_exec(|cmd| RawExecResult {
                exit_code: if cmd == "pwd" { 0 } else { 1 },
                stdout: String::new(),
                stderr: String::new(),
                success: true,
            }),
        PathBuf::from("/nonexistent"),
    );
    let info = lifecycle.ensure_running(&sid()).await.unwrap();
    assert_eq!(info.id.as_str(), "box");
}

// delete disposes the registry binding.
#[tokio::test]
async fn delete_disposes_binding() {
    let registry = ProviderRegistry::new();
    let adapter: Arc<dyn ProviderAdapter> = Arc::new(MockAdapter::new().with_id("box"));
    registry.set_default(Arc::clone(&adapter));
    registry.register(&sid(), Arc::clone(&adapter));
    let lifecycle = SandboxLifecycle::new(
        Arc::new(DaemonClient::new(Arc::new(registry))),
        PathBuf::from("/nonexistent"),
    );
    assert!(lifecycle.daemon.registry().has(&sid()));
    lifecycle.delete(&sid()).await.unwrap();
    assert!(!lifecycle.daemon.registry().has(&sid()));
}

// ensure_git: git already present → early return; install failure → fail-open.
#[tokio::test]
async fn ensure_git_present_and_fail_open() {
    // present.
    let lifecycle = lifecycle_with(
        MockAdapter::new().with_exec(|_cmd| RawExecResult {
            exit_code: 0,
            stdout: "ok".to_owned(),
            stderr: String::new(),
            success: true,
        }),
        PathBuf::from("/nonexistent"),
    );
    lifecycle.ensure_git(&sid()).await.unwrap();

    // missing + install fails → still Ok (fail-open).
    let lifecycle = lifecycle_with(
        MockAdapter::new().with_exec(|cmd| {
            if cmd.contains("command -v git") {
                RawExecResult {
                    exit_code: 0,
                    stdout: "missing".to_owned(),
                    stderr: String::new(),
                    success: true,
                }
            } else {
                RawExecResult {
                    exit_code: 1,
                    stdout: String::new(),
                    stderr: "no package manager".to_owned(),
                    success: false,
                }
            }
        }),
        PathBuf::from("/nonexistent"),
    );
    lifecycle.ensure_git(&sid()).await.unwrap();
}

// GC-05: the background upload overlaps ensure_git, the drain swallows its
// error, and the sequential bootstrap (step D) surfaces the fail-closed
// ArtifactHashMismatch.
#[tokio::test]
async fn setup_overlap_drains_and_bootstrap_is_authoritative() {
    let tmp = std::env::temp_dir().join(format!("eosd-lc-{}", uuid::Uuid::new_v4().simple()));
    tokio::fs::create_dir_all(&tmp).await.unwrap();
    tokio::fs::write(tmp.join("eosd-linux-amd64"), b"fake")
        .await
        .unwrap();
    let adapter = MockAdapter::new().with_id("box").with_exec(|cmd| {
        let stdout = if cmd.contains("uname -m") {
            "x86_64"
        } else if cmd.contains("command -v git") {
            "ok"
        } else {
            ""
        };
        RawExecResult {
            exit_code: 0,
            stdout: stdout.to_owned(),
            stderr: String::new(),
            success: true,
        }
    });
    let calls = adapter.call_log();
    let lifecycle = lifecycle_with(adapter, tmp.clone());
    let err = lifecycle
        .setup_post_lifecycle(&sid(), Some("/workspace"), LifecyclePhase::Create)
        .await
        .unwrap_err();
    assert!(matches!(err, SandboxHostError::ArtifactHashMismatch { .. }));
    let log = calls.lock().unwrap().clone();
    assert!(
        log.iter().any(|c| c.contains("command -v git")),
        "ensure_git ran"
    );
    assert!(
        log.iter().any(|c| c.contains("uname -m")),
        "eosd upload probed arch"
    );
    tokio::fs::remove_dir_all(&tmp).await.ok();
}

#[test]
fn readiness_gate_requires_all_three() {
    let ok: JsonObject = serde_json::from_value(serde_json::json!({
        "ready": true,
        "probes": [{"name": "control_plane", "status": "ok", "details": {"manifest_version": 2}}]
    }))
    .unwrap();
    assert!(require_workspace_base_ready(&ok).is_ok());

    // not ready.
    let not_ready: JsonObject = serde_json::from_value(serde_json::json!({
        "ready": false,
        "probes": [{"name": "control_plane", "status": "ok", "details": {"manifest_version": 2}}]
    }))
    .unwrap();
    assert!(require_workspace_base_ready(&not_ready).is_err());

    // control_plane down.
    let down: JsonObject = serde_json::from_value(serde_json::json!({
        "ready": true,
        "probes": [{"name": "control_plane", "status": "down", "details": {"manifest_version": 2}}]
    }))
    .unwrap();
    assert!(require_workspace_base_ready(&down).is_err());

    // manifest_version < 1.
    let v0: JsonObject = serde_json::from_value(serde_json::json!({
        "ready": true,
        "probes": [{"name": "control_plane", "status": "ok", "details": {"manifest_version": 0}}]
    }))
    .unwrap();
    assert!(require_workspace_base_ready(&v0).is_err());

    // numeric-string manifest_version is tolerated.
    let str_ver: JsonObject = serde_json::from_value(serde_json::json!({
        "ready": true,
        "probes": [{"name": "control_plane", "status": "ok", "details": {"manifest_version": "3"}}]
    }))
    .unwrap();
    assert!(require_workspace_base_ready(&str_ver).is_ok());

    // truthy-but-non-bool `ready` does NOT pass.
    let truthy: JsonObject = serde_json::from_value(serde_json::json!({
        "ready": 1,
        "probes": [{"name": "control_plane", "status": "ok", "details": {"manifest_version": 2}}]
    }))
    .unwrap();
    assert!(require_workspace_base_ready(&truthy).is_err());
}
