use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use super::common::SandboxRequestBase;

/// Categorical isolated-workspace lifecycle error.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct LifecycleError {
    /// Error category.
    pub kind: String,
    /// Error message.
    #[serde(default)]
    pub message: String,
    /// Structured detail fields.
    #[serde(default)]
    pub details: BTreeMap<String, String>,
}

/// Base result for isolated-workspace lifecycle operations (distinct from OCC
/// conflicts).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct LifecycleResultBase {
    /// Whether the lifecycle operation succeeded (defaults to `true`).
    #[serde(default = "default_true")]
    pub success: bool,
    /// Operation timings.
    #[serde(default)]
    pub timings: BTreeMap<String, f64>,
    /// Lifecycle error, when the operation failed.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<LifecycleError>,
}

/// Enter an isolated workspace.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct EnterIsolatedWorkspaceRequest {
    /// Caller identity / description / invocation id.
    #[serde(flatten)]
    pub base: SandboxRequestBase,
    /// `LayerStack` root to base the isolated workspace on.
    pub layer_stack_root: String,
}

/// Result of [`EnterIsolatedWorkspaceRequest`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct EnterIsolatedWorkspaceResult {
    /// Common lifecycle result fields.
    #[serde(flatten)]
    pub base: LifecycleResultBase,
    /// Manifest version of the entered workspace.
    #[serde(default)]
    pub manifest_version: String,
    /// Root hash of the entered workspace manifest.
    #[serde(default)]
    pub manifest_root_hash: String,
}

/// Exit an isolated workspace.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ExitIsolatedWorkspaceRequest {
    /// Caller identity / description / invocation id.
    #[serde(flatten)]
    pub base: SandboxRequestBase,
    /// Grace period in seconds before forcing teardown (defaults to `5.0`).
    #[serde(default = "default_grace_s")]
    pub grace_s: f64,
}

/// Result of [`ExitIsolatedWorkspaceRequest`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ExitIsolatedWorkspaceResult {
    /// Common lifecycle result fields.
    #[serde(flatten)]
    pub base: LifecycleResultBase,
    /// Bytes evicted from the upperdir on teardown.
    #[serde(default)]
    pub evicted_upperdir_bytes: u64,
    /// Total lifetime of the isolated workspace, seconds.
    #[serde(default)]
    pub lifetime_s: f64,
    /// Per-phase teardown timings, milliseconds.
    #[serde(default)]
    pub phases_ms: BTreeMap<String, f64>,
}

fn default_true() -> bool {
    true
}

fn default_grace_s() -> f64 {
    5.0
}
