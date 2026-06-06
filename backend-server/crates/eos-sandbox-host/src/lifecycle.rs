//! `SandboxLifecycle`: container `create`/`start`/`stop`/`delete`/`set_labels`/`ensure_running`
//! plus the `setup_post_lifecycle` orchestration (faithful port of
//! `sandbox/host/lifecycle.py` + `bootstrap.py`).
//!
//! Setup sequence (identical for create + start): launch the background eosd
//! upload (GC-05 `JoinSet`/spawn) → best-effort `ensure_git` → drain the upload
//! (errors swallowed) → sequential authoritative eosd upload (fail-closed) →
//! `ensure_workspace_base` bind + readiness gate (fail-closed).

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use eos_types::{JsonObject, SandboxId};
use serde_json::Value;

use crate::bootstrap_artifact::ensure_daemon_bootstrap;
use crate::daemon_client::{DaemonClient, DEFAULT_LAYER_STACK_ROOT};
use crate::error::SandboxHostError;
use crate::provider::{CreateSandboxSpec, ExecOpts, Labels, SandboxInfo};

const BUNDLE_UPLOAD_JOIN_TIMEOUT: Duration = Duration::from_secs(60);
const WORKSPACE_BINDING_MISMATCH: &str = "workspace binding points at a different workspace";
const ENSURE_WORKSPACE_BASE_TIMEOUT_S: u32 = 180;
const RUNTIME_READY_TIMEOUT_S: u32 = 60;
const ENSURE_GIT_PROBE_TIMEOUT_S: u32 = 10;
const ENSURE_GIT_INSTALL_TIMEOUT_S: u32 = 120;
const ENSURE_RUNNING_PROBE_TIMEOUT_S: u32 = 10;

/// The container-side install-git script, shipped with the binary.
const INSTALL_GIT_SCRIPT: &str = include_str!("install_git.sh");

#[derive(Debug, Clone, Copy)]
enum LifecyclePhase {
    Create,
    Start,
}

/// Per-process container lifecycle orchestration over the provider registry +
/// daemon client. Holds no lock itself; registry guards are cloned-and-dropped
/// before any `.await` (spec §7).
#[derive(Debug)]
pub struct SandboxLifecycle {
    daemon: Arc<DaemonClient>,
    /// Directory holding the pinned `eosd-linux-{arch}` binaries (the Python
    /// `<repo>/sandbox/dist`), supplied by the composition root.
    artifact_dir: PathBuf,
}

impl SandboxLifecycle {
    /// Build a lifecycle over a shared daemon client and the eosd artifact dir.
    #[must_use]
    pub fn new(daemon: Arc<DaemonClient>, artifact_dir: PathBuf) -> Self {
        Self {
            daemon,
            artifact_dir,
        }
    }

    /// Create a container, register it under the default adapter, and run
    /// post-create setup.
    pub async fn create(&self, spec: &CreateSandboxSpec) -> Result<SandboxInfo, SandboxHostError> {
        let provider = self.daemon.registry().default()?;
        let info = provider.create(spec).await?;
        // The Python `if sandbox_id:` gate is structurally always true here:
        // `SandboxInfo.id` is a non-empty `SandboxId` by construction.
        self.daemon
            .registry()
            .register(&info.id, Arc::clone(&provider));
        self.setup_post_lifecycle(
            &info.id,
            info.project_dir.as_deref(),
            LifecyclePhase::Create,
        )
        .await?;
        Ok(info)
    }

    /// Start a container and run post-start setup.
    pub async fn start(&self, id: &SandboxId) -> Result<SandboxInfo, SandboxHostError> {
        let adapter = self.daemon.registry().adapter(id)?;
        let info = adapter.start(id).await?;
        self.setup_post_lifecycle(id, info.project_dir.as_deref(), LifecyclePhase::Start)
            .await?;
        Ok(info)
    }

    /// Stop a container (pure delegation, no setup/cleanup).
    pub async fn stop(&self, id: &SandboxId) -> Result<SandboxInfo, SandboxHostError> {
        self.daemon.registry().adapter(id)?.stop(id).await
    }

    /// Delete a container and dispose its registry binding.
    pub async fn delete(&self, id: &SandboxId) -> Result<(), SandboxHostError> {
        self.daemon.registry().adapter(id)?.delete(id).await?;
        // The Python `forget_plugin_dispatch_state`/`forget_plugin_install_state`
        // calls are dropped: those pop host-process plugin caches, and the Rust
        // host ports no plugin internals (GC-03). dispose removes the binding.
        self.daemon.registry().dispose(id);
        Ok(())
    }

    /// Replace a container's labels (provider-specific semantics).
    pub async fn set_labels(
        &self,
        id: &SandboxId,
        labels: &Labels,
    ) -> Result<SandboxInfo, SandboxHostError> {
        self.daemon
            .registry()
            .adapter(id)?
            .set_labels(id, labels)
            .await
    }

    /// Best-effort recovery: probe the sandbox, restart + re-setup if unhealthy.
    pub async fn ensure_running(&self, id: &SandboxId) -> Result<SandboxInfo, SandboxHostError> {
        let adapter = self.daemon.registry().adapter(id)?;
        let info = adapter.get(id).await?;
        match adapter
            .exec(id, "pwd", &probe_opts(ENSURE_RUNNING_PROBE_TIMEOUT_S))
            .await
        {
            Ok(resp) if resp.exit_code == 0 => return Ok(info), // healthy
            Ok(_) => {} // non-zero: fall through to recovery
            Err(err) => {
                tracing::warn!(
                    sandbox = id.as_str(),
                    ?err,
                    "probe failed; attempting restart recovery"
                );
            }
        }
        if let Err(err) = adapter.start(id).await {
            tracing::debug!(
                sandbox = id.as_str(),
                ?err,
                "start during recovery raised; refreshing handle"
            );
        }
        let info = adapter.get(id).await?;
        let workspace = info.project_dir.clone().unwrap_or_default();
        self.setup_post_lifecycle(id, Some(&workspace), LifecyclePhase::Start)
            .await?;
        Ok(info)
    }

    // --- setup orchestration --------------------------------------------------

    async fn setup_post_lifecycle(
        &self,
        id: &SandboxId,
        workspace_root: Option<&str>,
        phase: LifecyclePhase,
    ) -> Result<(), SandboxHostError> {
        tracing::debug!(
            ?phase,
            sandbox = id.as_str(),
            "running sandbox post-lifecycle bootstrap"
        );
        // (A) launch the background eosd upload (GC-05) BEFORE ensure_git.
        let upload = self.start_runtime_bundle_upload(id, workspace_root);
        // (B) best-effort git install (runs concurrently with the upload).
        self.ensure_git(id).await?;
        // (C) drain the overlap — errors swallowed by design (step D retries).
        finish_runtime_bundle_upload(upload, id).await;
        // (D) sequential authoritative eosd upload (fail-closed).
        self.run_runtime_bootstrap(id, workspace_root).await?;
        // (E) bind workspace + readiness gate (fail-closed).
        self.ensure_workspace_base(id, workspace_root).await?;
        Ok(())
    }

    /// (A) Spawn the background eosd-upload task, or `None` when guarded out
    /// (empty workspace or id). Errors are swallowed at drain (the sequential
    /// bootstrap is authoritative).
    fn start_runtime_bundle_upload(
        &self,
        id: &SandboxId,
        workspace_root: Option<&str>,
    ) -> Option<tokio::task::JoinHandle<Result<(), SandboxHostError>>> {
        if workspace_root.map(str::trim).unwrap_or("").is_empty() || id.as_str().is_empty() {
            return None;
        }
        let daemon = Arc::clone(&self.daemon);
        let artifact_dir = self.artifact_dir.clone();
        let id = id.clone();
        Some(tokio::spawn(async move {
            let adapter = daemon.registry().adapter(&id)?;
            ensure_daemon_bootstrap(&*adapter, &id, &artifact_dir).await
        }))
    }

    /// (D) The sequential, authoritative eosd upload.
    async fn run_runtime_bootstrap(
        &self,
        id: &SandboxId,
        workspace_root: Option<&str>,
    ) -> Result<(), SandboxHostError> {
        if workspace_root.map(str::trim).unwrap_or("").is_empty() || id.as_str().is_empty() {
            tracing::debug!(
                sandbox = id.as_str(),
                "runtime bootstrap skipped: no project_dir"
            );
            return Ok(());
        }
        let adapter = self.daemon.registry().adapter(id)?;
        ensure_daemon_bootstrap(&*adapter, id, &self.artifact_dir).await
    }

    /// (E) Bind the workspace base and gate on runtime readiness.
    async fn ensure_workspace_base(
        &self,
        id: &SandboxId,
        workspace_root: Option<&str>,
    ) -> Result<(), SandboxHostError> {
        let workspace = workspace_root.map(str::trim).unwrap_or("");
        if workspace.is_empty() || id.as_str().is_empty() {
            tracing::debug!(
                sandbox = id.as_str(),
                "workspace base skipped: no project_dir"
            );
            return Ok(());
        }
        let mut args = JsonObject::new();
        args.insert(
            "workspace_root".to_owned(),
            Value::String(workspace.to_owned()),
        );
        match self
            .daemon
            .call_daemon_api(
                id,
                "api.ensure_workspace_base",
                args.clone(),
                ENSURE_WORKSPACE_BASE_TIMEOUT_S,
                DEFAULT_LAYER_STACK_ROOT,
            )
            .await
        {
            Ok(_) => {}
            Err(SandboxHostError::DaemonDispatch { message, .. })
                if message.contains(WORKSPACE_BINDING_MISMATCH) =>
            {
                tracing::info!(
                    sandbox = id.as_str(),
                    "rebuilding workspace base after binding mismatch"
                );
                let mut rebuild = args;
                rebuild.insert("reset".to_owned(), Value::Bool(true));
                self.daemon
                    .call_daemon_api(
                        id,
                        "api.build_workspace_base",
                        rebuild,
                        ENSURE_WORKSPACE_BASE_TIMEOUT_S,
                        DEFAULT_LAYER_STACK_ROOT,
                    )
                    .await?;
            }
            Err(err) => return Err(err),
        }
        let readiness = self
            .daemon
            .call_daemon_api(
                id,
                "api.runtime.ready",
                JsonObject::new(),
                RUNTIME_READY_TIMEOUT_S,
                DEFAULT_LAYER_STACK_ROOT,
            )
            .await?;
        require_workspace_base_ready(&readiness)
    }

    /// (B) Install git if missing. Install failures are fail-open (logged);
    /// adapter/transport failures propagate (the sandbox is broken).
    async fn ensure_git(&self, id: &SandboxId) -> Result<(), SandboxHostError> {
        if id.as_str().is_empty() {
            return Ok(());
        }
        let adapter = self.daemon.registry().adapter(id)?;
        let probe = adapter
            .exec(
                id,
                "command -v git >/dev/null 2>&1 && echo ok || echo missing",
                &probe_opts(ENSURE_GIT_PROBE_TIMEOUT_S),
            )
            .await?;
        if probe.stdout.contains("ok") {
            return Ok(());
        }
        let install = adapter
            .exec(
                id,
                INSTALL_GIT_SCRIPT,
                &probe_opts(ENSURE_GIT_INSTALL_TIMEOUT_S),
            )
            .await?;
        if install.exit_code != 0 {
            // fail-open: mirror the Python `RuntimeError` swallow.
            tracing::warn!(
                sandbox = id.as_str(),
                exit_code = install.exit_code,
                "git bootstrap failed; continuing"
            );
        }
        Ok(())
    }
}

fn probe_opts(timeout_s: u32) -> ExecOpts {
    ExecOpts {
        cwd: None,
        timeout: Some(Duration::from_secs(u64::from(timeout_s))),
    }
}

/// (C) Drain the background upload, swallowing all errors (timeout and task
/// failure both log and move on — step D is the authoritative retry).
async fn finish_runtime_bundle_upload(
    handle: Option<tokio::task::JoinHandle<Result<(), SandboxHostError>>>,
    id: &SandboxId,
) {
    let Some(handle) = handle else { return };
    match tokio::time::timeout(BUNDLE_UPLOAD_JOIN_TIMEOUT, handle).await {
        Ok(Ok(Ok(()))) => {
            tracing::info!(sandbox = id.as_str(), "background bundle upload joined");
        }
        Ok(Ok(Err(err))) => {
            tracing::error!(
                sandbox = id.as_str(),
                ?err,
                "background upload failed; sequential bootstrap will retry"
            );
        }
        Ok(Err(join_err)) => {
            tracing::error!(
                sandbox = id.as_str(),
                ?join_err,
                "background upload task panicked; sequential bootstrap will retry"
            );
        }
        Err(_elapsed) => {
            tracing::warn!(
                sandbox = id.as_str(),
                "background upload did not complete in time; sequential bootstrap will retry"
            );
        }
    }
}

fn runtime_probe<'a>(readiness: &'a JsonObject, name: &str) -> Option<&'a JsonObject> {
    readiness
        .get("probes")
        .and_then(Value::as_array)?
        .iter()
        .filter_map(Value::as_object)
        .find(|probe| probe.get("name").and_then(Value::as_str) == Some(name))
}

/// The fail-closed readiness gate: `ready == true`, the `control_plane` probe is
/// `ok`, and its `manifest_version >= 1`.
pub(crate) fn require_workspace_base_ready(readiness: &JsonObject) -> Result<(), SandboxHostError> {
    let control_plane = runtime_probe(readiness, "control_plane");
    let manifest_version = control_plane
        .and_then(|cp| cp.get("details"))
        .and_then(Value::as_object)
        .and_then(|details| details.get("manifest_version"))
        .map(manifest_version_as_int)
        .unwrap_or(0);
    let ready = readiness.get("ready") == Some(&Value::Bool(true));
    let control_ok = control_plane
        .and_then(|cp| cp.get("status"))
        .and_then(Value::as_str)
        == Some("ok");
    if ready && control_ok && manifest_version >= 1 {
        Ok(())
    } else {
        let mut details = JsonObject::new();
        details.insert("response".to_owned(), Value::Object(readiness.clone()));
        Err(SandboxHostError::DaemonNotReady { details })
    }
}

/// `int(details["manifest_version"] or 0)` — tolerate a number or numeric string.
fn manifest_version_as_int(value: &Value) -> i64 {
    match value {
        Value::Number(n) => n.as_i64().unwrap_or(0),
        Value::String(s) => s.trim().parse().unwrap_or(0),
        _ => 0,
    }
}

#[cfg(test)]
mod tests {
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
}
