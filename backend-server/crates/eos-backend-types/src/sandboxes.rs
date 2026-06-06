//! Sanitized sandbox view returned by the public sandbox API.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use eos_types::{RequestId, SandboxId, UtcDateTime};

/// Coarse backend-tracked lifecycle phase of a sandbox.
///
/// These are exactly the states `SandboxManager` constructs and exposes through
/// the sanitized [`SandboxView`]; transient `Provisioning` (held under the
/// acquire lock, never observable) and post-teardown `Destroyed` (the entry is
/// dropped, not re-stated) are intentionally absent from the public surface.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum SandboxState {
    /// Provisioned and idle (no active run).
    Ready,
    /// Bound to at least one active run.
    Active,
    /// No active run but retained against destruction.
    Retained,
    /// Teardown in progress.
    Destroying,
}

/// Sanitized public view of a backend-owned sandbox.
///
/// Carries only lifecycle and ownership facts. It deliberately exposes no daemon
/// connection material and no credentials (AC4); those stay internal to the host
/// and backend runtime crates and never cross the HTTP API.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct SandboxView {
    /// Sandbox id.
    pub sandbox_id: SandboxId,
    /// Coarse lifecycle phase.
    pub state: SandboxState,
    /// The request that owns/created the sandbox, if any.
    pub owner_request_id: Option<RequestId>,
    /// Requests currently holding an active reference.
    pub active_request_ids: Vec<RequestId>,
    /// Total active + retained references.
    pub ref_count: u32,
    /// When the sandbox was created.
    pub created_at: UtcDateTime,
    /// When the sandbox was last used by a run.
    pub last_used_at: UtcDateTime,
    /// Whether the sandbox is destroyed once the last reference is released.
    pub destroy_on_finish: bool,
}
