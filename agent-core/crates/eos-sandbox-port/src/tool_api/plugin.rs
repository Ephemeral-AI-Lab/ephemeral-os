//! Pure plugin helpers: build daemon payloads -> call typed or dynamic daemon ops.

use eos_types::{JsonObject, SandboxId};
use serde::Serialize;
use serde_json::Value;

use crate::error::SandboxPortError;
use crate::models::{Intent, SandboxRequestBase};
use crate::ops::DaemonOp;
use crate::tool_api::parse::daemon_request_identity_fields;
use crate::transport::SandboxTransport;

/// Dependency isolation scope for a plugin package.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum PluginDependencyScope {
    /// Dependencies live under `/eos/runtime/packages/<plugin>/<digest>/`.
    PackageDigest,
}

/// Package-root contract for a plugin payload.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct PluginPackageContract {
    /// Package-relative runtime directory.
    pub runtime_dir: String,
    /// Package dependency isolation scope.
    pub dependency_scope: PluginDependencyScope,
}

/// Optional setup command executed by the daemon after package publish.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct PluginSetupDescriptor {
    /// Command vector executed by the daemon setup runner.
    pub command: Vec<String>,
    /// Package-relative setup working directory.
    pub working_dir: String,
    /// Digest recorded after setup completes successfully.
    pub setup_marker_digest: String,
    /// Setup command timeout in milliseconds.
    pub timeout_ms: u64,
}

/// Daemon-managed plugin service mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum PluginServiceMode {
    /// Long-lived read-only service refreshed against workspace snapshots.
    WorkspaceSnapshotRefresh,
    /// Stateless/write worker invoked through a fresh per-operation overlay.
    OneshotOverlay,
}

/// Strategy used when a workspace-snapshot service becomes stale.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum PluginRefreshStrategy {
    /// Remount the service workspace and notify the process.
    RemountWorkspaceAndNotify,
    /// Remount the service workspace without an explicit process notification.
    RemountWorkspace,
    /// Restart the service process on the new snapshot.
    RestartService,
}

/// One daemon-managed service declared by a plugin package.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct PluginServiceDescriptor {
    /// Service identifier unique within the plugin.
    pub service_id: String,
    /// Service profile digest used in the daemon service identity key.
    pub service_profile_digest: String,
    /// Service lifecycle mode.
    pub service_mode: PluginServiceMode,
    /// Workspace refresh strategy.
    pub refresh_strategy: PluginRefreshStrategy,
    /// Service command vector, package-relative where appropriate.
    pub command: Vec<String>,
    /// Optional package-relative working directory.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub working_dir: Option<String>,
    /// PPC protocol version spoken by the service process.
    pub ppc_protocol_version: u32,
}

/// One public `plugin.<plugin>.<op>` operation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct PluginOperationDescriptor {
    /// Operation name without the `plugin.<plugin>.` prefix.
    pub op_name: String,
    /// Declared sandbox execution intent.
    pub intent: Intent,
    /// Whether the daemon should run write operations through an automatic overlay.
    pub auto_workspace_overlay: bool,
    /// Optional daemon service backing this operation.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub service_id: Option<String>,
    /// Optional per-operation timeout in milliseconds.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeout_ms: Option<u64>,
}

/// Neutral daemon manifest sent inside `api.plugin.ensure`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct PluginManifestDescriptor {
    /// Plugin identifier.
    pub plugin_id: String,
    /// Plugin package version.
    pub plugin_version: String,
    /// Package content digest.
    pub plugin_digest: String,
    /// Package root contract.
    pub package: PluginPackageContract,
    /// Optional package setup contract.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub setup: Option<PluginSetupDescriptor>,
    /// Service declarations.
    pub services: Vec<PluginServiceDescriptor>,
    /// Operation declarations.
    pub operations: Vec<PluginOperationDescriptor>,
}

/// One regular file in a plugin package source tree.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PluginPackageFile {
    /// Package-relative file path.
    pub path: String,
    /// File bytes.
    pub contents: Vec<u8>,
    /// POSIX file mode.
    pub mode: u32,
}

/// Catalog-produced plugin package source tree.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PluginPackageTree {
    /// Regular files uploaded into the package staging root.
    pub files: Vec<PluginPackageFile>,
}

/// Neutral package descriptor consumed by host setup and daemon ensure APIs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PluginPackageDescriptor {
    /// Daemon manifest for `api.plugin.ensure`.
    pub manifest: PluginManifestDescriptor,
    /// Package source tree for the host cold upload path.
    pub package_tree: PluginPackageTree,
}

/// A plugin manifest ensure request.
#[derive(Debug, Clone)]
pub struct PluginEnsureRequest {
    /// Shared sandbox caller identity for this tool call.
    pub base: SandboxRequestBase,
    /// Repository root visible to the plugin runtime.
    pub workspace_root: String,
    /// Manifest consumed by the daemon `api.plugin.ensure` operation.
    pub manifest: PluginManifestDescriptor,
    /// Optional staged package root for the cold publish path.
    pub staged_package_root: Option<String>,
    /// Whether daemon-managed service processes should be started immediately.
    pub start_services: bool,
    /// Daemon ensure timeout in seconds.
    pub timeout_s: u32,
}

/// A high-level plugin package ensure request.
#[derive(Debug, Clone)]
pub struct PluginPackageEnsureRequest {
    /// Shared sandbox caller identity for this tool call.
    pub base: SandboxRequestBase,
    /// Repository root visible to the plugin runtime.
    pub workspace_root: String,
    /// Neutral package descriptor from the catalog.
    pub package: PluginPackageDescriptor,
    /// Whether daemon-managed service processes should be started immediately.
    pub start_services: bool,
    /// Daemon ensure timeout in seconds.
    pub timeout_s: u32,
}

impl PluginPackageEnsureRequest {
    /// Convert this package request into one daemon ensure request.
    #[must_use]
    pub fn into_plugin_ensure_request(
        self,
        staged_package_root: Option<String>,
    ) -> PluginEnsureRequest {
        PluginEnsureRequest {
            base: self.base,
            workspace_root: self.workspace_root,
            manifest: self.package.manifest,
            staged_package_root,
            start_services: self.start_services,
            timeout_s: self.timeout_s,
        }
    }
}

/// A catalog plugin operation request.
#[derive(Debug, Clone)]
pub struct PluginDispatchRequest {
    /// Shared sandbox caller identity for this tool call.
    pub base: SandboxRequestBase,
    /// Plugin id, for example `lsp`.
    pub plugin_id: String,
    /// Plugin operation name, for example `hover`.
    pub op_name: String,
    /// Declared sandbox execution intent.
    pub intent: Intent,
    /// Repository root visible to the plugin runtime.
    pub workspace_root: String,
    /// Model-facing tool arguments.
    pub args: JsonObject,
    /// Daemon dispatch timeout in seconds.
    pub timeout_s: u32,
}

/// Ensure one plugin manifest is loaded in the sandbox daemon.
///
/// The daemon op is the built-in `api.plugin.ensure`. The transport injects the
/// standard layer-stack root; this helper adds the standard daemon caller
/// identity, workspace root, manifest, and service-start policy.
pub async fn plugin_ensure(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    request: PluginEnsureRequest,
) -> Result<JsonObject, SandboxPortError> {
    let timeout_s = request.timeout_s;
    let payload = plugin_ensure_payload(request)?;
    transport
        .call(sandbox_id, DaemonOp::PluginEnsure, payload, timeout_s)
        .await
}

/// Ensure a catalog plugin package is available in the sandbox.
///
/// The default transport implementation performs only the daemon ensure call.
/// Host-backed transports may override this path to run the warm probe, upload a
/// package tree privately when requested, and retry the daemon ensure with a
/// staged package root.
pub async fn ensure_plugin_package(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    request: PluginPackageEnsureRequest,
) -> Result<JsonObject, SandboxPortError> {
    transport.ensure_plugin_package(sandbox_id, request).await
}

pub(crate) fn plugin_ensure_payload(
    request: PluginEnsureRequest,
) -> Result<JsonObject, SandboxPortError> {
    let mut payload = daemon_request_identity_fields(&request.base);
    payload.insert(
        "workspace_root".to_owned(),
        Value::String(request.workspace_root),
    );
    let manifest = serde_json::to_value(request.manifest)
        .map_err(|err| SandboxPortError::decode(format!("plugin manifest encode failed: {err}")))?;
    payload.insert("manifest".to_owned(), manifest);
    if let Some(staged_package_root) = request.staged_package_root {
        payload.insert(
            "staged_package_root".to_owned(),
            Value::String(staged_package_root),
        );
    }
    payload.insert(
        "start_services".to_owned(),
        Value::Bool(request.start_services),
    );
    Ok(payload)
}

/// Dispatch one catalog plugin operation through the sandbox daemon.
///
/// The dynamic daemon op is `plugin.<plugin_id>.<op_name>`. The caller supplies
/// `args` as the model-facing tool payload; this helper adds the standard daemon
/// caller identity plus plugin intent/workspace metadata.
pub async fn plugin_dispatch(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    request: PluginDispatchRequest,
) -> Result<JsonObject, SandboxPortError> {
    let mut payload = daemon_request_identity_fields(&request.base);
    payload.extend(request.args);
    payload.insert(
        "intent".to_owned(),
        Value::String(request.intent.as_wire().to_owned()),
    );
    payload.insert(
        "workspace_root".to_owned(),
        Value::String(request.workspace_root),
    );
    let op = format!("plugin.{}.{}", request.plugin_id, request.op_name);
    transport
        .call_dynamic(sandbox_id, &op, payload, request.timeout_s)
        .await
}
