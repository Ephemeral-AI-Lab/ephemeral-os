//! The provider seam (`ProviderAdapter`), its value types, and the typed
//! [`ContextPreparer`] fixed point (GC-07).
//!
//! `ProviderAdapter` is the OCP/LSP seam: the production Docker adapter and a
//! `#[cfg(test)]` mock are substitutable behind `Arc<dyn ProviderAdapter>`. The
//! trait is **sealed** (`api-sealed-trait`) so only in-crate types implement it;
//! a future production provider needs an explicit plan (spec §1).

use std::collections::BTreeMap;
use std::time::Duration;

use async_trait::async_trait;
use eos_types::{JsonObject, SandboxId};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::error::SandboxHostError;

/// Container/sandbox label map (`BTreeMap` for deterministic order).
pub type Labels = BTreeMap<String, String>;

/// The sandbox backend selector (spec-conventions §4: `sandbox_provider`, never
/// bare `provider`). `#[non_exhaustive]` so dispatch sites carry a catch-all
/// arm, but the Rust migration ships only `Docker`.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, Default, Serialize, Deserialize, JsonSchema,
)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum ProviderKind {
    /// The Docker sandbox backend — the only supported Rust provider.
    #[default]
    Docker,
}

impl ProviderKind {
    /// The wire/string form of this kind (`"docker"`).
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Docker => "docker",
        }
    }
}

/// Arguments to [`ProviderAdapter::create`] (mirrors the Python `create(...)`
/// kwargs; `language` defaults to `"python"`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct CreateSandboxSpec {
    /// Human/display name for the container.
    pub name: String,
    /// Optional snapshot/image tag to create from.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub snapshot: Option<String>,
    /// Optional explicit image override.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub image: Option<String>,
    /// Runtime language profile (defaults to `"python"`).
    #[serde(default = "default_language")]
    pub language: String,
    /// Environment variables injected into the container.
    #[serde(default)]
    pub env_vars: BTreeMap<String, String>,
    /// Labels applied at create time.
    #[serde(default)]
    pub labels: Labels,
    /// Optional Docker platform string (Docker only).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub platform: Option<String>,
}

fn default_language() -> String {
    "python".to_owned()
}

impl Default for CreateSandboxSpec {
    fn default() -> Self {
        Self {
            name: String::new(),
            snapshot: None,
            image: None,
            language: default_language(),
            env_vars: BTreeMap::new(),
            labels: Labels::new(),
            platform: None,
        }
    }
}

/// Canonical serialized sandbox/container shape returned by the provider.
///
/// Canonical-normalization drops the Docker `_serialize_container` `docker_init`
/// (`HostConfig.Init`) field because no consumer in this crate needs it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct SandboxInfo {
    /// Provider/container id.
    pub id: SandboxId,
    /// Container name with any leading `/` stripped (Docker).
    pub name: String,
    /// Container image (`Config.Image`).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub image: Option<String>,
    /// Snapshot tag from `labels["snapshot"]`.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub snapshot: Option<String>,
    /// Normalized lowercase container state (`status` / `state`).
    pub state: String,
    /// Container/sandbox labels.
    #[serde(default)]
    pub labels: Labels,
    /// Project/working directory (`labels["project_dir"]` or `WorkingDir`).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub project_dir: Option<String>,
    /// Whether this sandbox is app-managed (`labels["managed_by"] == "eos"`).
    pub managed_by_app: bool,
}

/// Docker host-side TCP path to the resident daemon (from
/// `get_daemon_tcp_endpoint`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct DaemonTcpEndpoint {
    /// Mapped host (`127.0.0.1`).
    pub host: String,
    /// Host-mapped port (`HostPort`).
    pub port: u16,
    /// Container-internal daemon port (`37657`).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub internal_port: Option<u16>,
    /// Daemon auth token (`EOS_DAEMON_AUTH_TOKEN` env).
    pub auth_token: String,
}

/// The `ProviderAdapter::exec` return — owned here (spec §5; sandbox-api drops
/// it as "a host concern"). Mirrors the exec-relevant fields of the Python
/// `RawExecResult(SandboxResultBase)`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct RawExecResult {
    /// Process exit code.
    pub exit_code: i32,
    /// Captured stdout.
    pub stdout: String,
    /// Captured stderr (decode default `""`).
    #[serde(default)]
    pub stderr: String,
    /// Whether the exec succeeded (`SandboxResultBase.success`, default `true`).
    #[serde(default = "default_true")]
    pub success: bool,
}

fn default_true() -> bool {
    true
}

impl Default for RawExecResult {
    fn default() -> Self {
        Self {
            exit_code: 0,
            stdout: String::new(),
            stderr: String::new(),
            success: true,
        }
    }
}

/// Options for a provider `exec`. Not a wire DTO (carries a `Duration`).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ExecOpts {
    /// Working directory for the command.
    pub cwd: Option<String>,
    /// Optional wall-clock timeout for the command.
    pub timeout: Option<Duration>,
}

/// Provider health snapshot (mirrors the Docker `get_health` dict).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ProviderHealth {
    /// The provider name (`"docker"`).
    pub provider: String,
    /// Whether the provider backend is reachable/healthy.
    pub healthy: bool,
    /// Docker server version, if reported.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub server_version: Option<String>,
    /// Number of running containers, if reported.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub containers_running: Option<u64>,
    /// Host kernel version, if reported.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub kernel_version: Option<String>,
    /// Host operating system, if reported.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub operating_system: Option<String>,
    /// Failure detail when `healthy` is false (Docker `get_health` fail-open
    /// path returns `{provider, healthy: false, error}`).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub error: Option<String>,
}

/// A signed-preview-URL result. Docker returns `{ url: None, reason }`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct PreviewUrl {
    /// The signed URL, or `None` for providers without one (Docker).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub url: Option<String>,
    /// Why the URL is absent, when it is.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub reason: Option<String>,
}

/// A provider snapshot/image listing entry (mirrors Docker `_serialize_image`:
/// `name == image == first repo tag`).
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize, JsonSchema)]
pub struct SnapshotInfo {
    /// Primary tag (first repo tag), or `None` if untagged.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub name: Option<String>,
    /// Alias of `name` (Docker `_serialize_image` emits both).
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub image: Option<String>,
    /// Image/snapshot id.
    pub id: String,
    /// All repository tags attached to the image/snapshot.
    #[serde(default)]
    pub tags: Vec<String>,
}

/// The typed context-preparer fixed point (GC-07): a closed enum, **not** a new
/// trait seam. Replaces the duck-typed Python `context_preparer(...) -> Any`.
#[derive(Debug, Clone)]
#[non_exhaustive]
pub enum ContextPreparer {
    /// The Docker provider's context preparer.
    Docker(DockerContextPreparer),
}

impl ContextPreparer {
    /// Inject provider-aware runtime metadata into `ctx` (sync path).
    pub fn prepare_context(&self, ctx: &mut JsonObject) -> Result<(), SandboxHostError> {
        match self {
            Self::Docker(p) => p.inject(ctx),
        }
        Ok(())
    }

    /// Inject provider-aware runtime metadata into `ctx` (async path). Kept async
    /// to preserve the Python `prepare_context_async` seam for future
    /// docker-client round-trips.
    pub async fn prepare_context_async(
        &self,
        ctx: &mut JsonObject,
    ) -> Result<(), SandboxHostError> {
        match self {
            Self::Docker(p) => p.inject(ctx),
        }
        Ok(())
    }
}

/// Docker context-preparer payload (GC-07 typed fixed point). Carries the
/// sandbox id and injects provider-neutral metadata into a tool context map.
///
/// Deviation from spec §6 (`pub(crate)`): made `pub` with a private field +
/// `#[non_exhaustive]` so it can sit in the public [`ContextPreparer::Docker`]
/// variant without tripping `private_interfaces`; it remains un-constructable
/// outside this crate. The deep container/workspace discovery the Python
/// `DockerContextPreparer` performs lives outside the host (protocol.py: such
/// orchestration is built on top of the provider, not inside it), so the Rust
/// fixed point injects only `sandbox_id` + `sandbox_provider`.
#[derive(Debug, Clone)]
#[non_exhaustive]
pub struct DockerContextPreparer {
    sandbox_id: SandboxId,
}

impl DockerContextPreparer {
    pub(crate) fn new(sandbox_id: SandboxId) -> Self {
        Self { sandbox_id }
    }

    fn inject(&self, ctx: &mut JsonObject) {
        ctx.insert(
            "sandbox_id".to_owned(),
            self.sandbox_id.as_str().to_owned().into(),
        );
        ctx.insert(
            "sandbox_provider".to_owned(),
            ProviderKind::Docker.as_str().to_owned().into(),
        );
    }
}

pub(crate) mod sealed {
    /// Seals [`super::ProviderAdapter`] (`api-sealed-trait`): only in-crate types
    /// implement it. External crates cannot name this trait, so they cannot
    /// implement the supertrait (see `tests/compile_fail`).
    pub trait Sealed {}
}

/// Container CRUD + exec primitives implemented by each sandbox provider (the
/// OCP/LSP seam). Sealed; `#[async_trait]` because it is stored as
/// `Arc<dyn ProviderAdapter>` in the registry (anchor §6 object-safety note).
///
/// Method-name mapping from the Python `ProviderAdapter` Protocol drops the
/// `get_` prefix per Rust API guidelines C-GETTER: `health` ← `get_health`,
/// `signed_preview_url` ← `get_signed_preview_url`, `build_logs_url` ←
/// `get_build_logs_url`, `daemon_tcp_endpoint` ← `get_daemon_tcp_endpoint`,
/// `kind()` ← the `name: str` class attribute.
#[async_trait]
pub trait ProviderAdapter: sealed::Sealed + Send + Sync + std::fmt::Debug {
    /// The backend kind this adapter speaks for.
    fn kind(&self) -> ProviderKind;

    /// Backend health/reachability snapshot.
    async fn health(&self) -> Result<ProviderHealth, SandboxHostError>;
    /// List available snapshots/images.
    async fn list_snapshots(&self) -> Result<Vec<SnapshotInfo>, SandboxHostError>;

    /// Create a container from `spec`.
    async fn create(&self, spec: &CreateSandboxSpec) -> Result<SandboxInfo, SandboxHostError>;
    /// Fetch one container by id.
    async fn get(&self, id: &SandboxId) -> Result<SandboxInfo, SandboxHostError>;
    /// List managed containers.
    async fn list(&self) -> Result<Vec<SandboxInfo>, SandboxHostError>;
    /// Start a stopped container.
    async fn start(&self, id: &SandboxId) -> Result<SandboxInfo, SandboxHostError>;
    /// Stop a running container.
    async fn stop(&self, id: &SandboxId) -> Result<SandboxInfo, SandboxHostError>;
    /// Delete a container.
    async fn delete(&self, id: &SandboxId) -> Result<(), SandboxHostError>;
    /// Replace a container's labels.
    async fn set_labels(
        &self,
        id: &SandboxId,
        labels: &Labels,
    ) -> Result<SandboxInfo, SandboxHostError>;

    /// A signed preview URL for `port` (Docker returns `url: None`).
    async fn signed_preview_url(
        &self,
        id: &SandboxId,
        port: u16,
    ) -> Result<PreviewUrl, SandboxHostError>;
    /// A build-logs URL, if the provider exposes one.
    async fn build_logs_url(&self, id: &SandboxId) -> Result<Option<String>, SandboxHostError>;
    /// Docker-only host→daemon TCP endpoint; default `None` for providers with
    /// no TCP daemon path.
    async fn daemon_tcp_endpoint(
        &self,
        id: &SandboxId,
    ) -> Result<Option<DaemonTcpEndpoint>, SandboxHostError> {
        let _ = id;
        Ok(None)
    }

    /// Execute `command` inside the container.
    async fn exec(
        &self,
        id: &SandboxId,
        command: &str,
        opts: &ExecOpts,
    ) -> Result<RawExecResult, SandboxHostError>;
    /// Stream a tar archive into `dest_dir` (the provider unpacks server-side).
    async fn put_archive(
        &self,
        id: &SandboxId,
        tar_stream: &[u8],
        dest_dir: &str,
    ) -> Result<(), SandboxHostError>;

    /// The typed context preparer for `id` (GC-07 fixed point).
    fn context_preparer(&self, id: &SandboxId) -> ContextPreparer;
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    #[test]
    fn provider_kind_serializes_to_docker() {
        assert_eq!(
            serde_json::to_value(ProviderKind::Docker).unwrap(),
            serde_json::json!("docker")
        );
        assert_eq!(ProviderKind::Docker.as_str(), "docker");
    }

    #[test]
    fn create_spec_defaults_language_to_python() {
        let spec = CreateSandboxSpec::default();
        assert_eq!(spec.language, "python");
        // serde default also fills language when absent.
        let parsed: CreateSandboxSpec = serde_json::from_value(serde_json::json!({
            "name": "box"
        }))
        .unwrap();
        assert_eq!(parsed.language, "python");
        assert_eq!(parsed.name, "box");
    }

    #[test]
    fn raw_exec_result_default_success_true() {
        let r = RawExecResult::default();
        assert!(r.success);
        assert_eq!(r.exit_code, 0);
        // decode default: missing `success`/`stderr` fail-open to the
        // construction defaults (true / "").
        let parsed: RawExecResult = serde_json::from_value(serde_json::json!({
            "exit_code": 3,
            "stdout": "hi"
        }))
        .unwrap();
        assert_eq!(parsed.exit_code, 3);
        assert_eq!(parsed.stdout, "hi");
        assert!(parsed.success);
        assert_eq!(parsed.stderr, "");
    }

    #[test]
    fn context_preparer_injects_docker_metadata() {
        let prep = ContextPreparer::Docker(DockerContextPreparer::new(
            "sb-1".parse().expect("non-empty id"),
        ));
        let mut ctx = JsonObject::new();
        prep.prepare_context(&mut ctx).expect("prepare");
        assert_eq!(ctx["sandbox_id"], serde_json::json!("sb-1"));
        assert_eq!(ctx["sandbox_provider"], serde_json::json!("docker"));
    }
}
