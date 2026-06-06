//! Run lifecycle status, run metadata, and the v1 user-request API DTOs.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use eos_types::{RequestId, SandboxId, UtcDateTime};

/// Backend-owned lifecycle status persisted in `run_meta.status`.
///
/// Agent-core's `RequestStatus` stays agent-core-owned; `Cancelled` is a
/// backend-local state and is never forced into agent-core state.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum BackendRunStatus {
    /// The request was accepted and `run_meta` was written before launch.
    Accepted,
    /// The agent-core run is in flight.
    Running,
    /// The run finished successfully.
    Done,
    /// The run finished with a failure.
    Failed,
    /// The run was cancelled by a backend-local cancellation request.
    Cancelled,
}

impl BackendRunStatus {
    /// The stable TEXT-column form (matches the `snake_case` serde form).
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Accepted => "accepted",
            Self::Running => "running",
            Self::Done => "done",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
        }
    }

    /// Parse the TEXT-column form, returning `None` for an unknown value.
    #[must_use]
    pub fn from_db(value: &str) -> Option<Self> {
        match value {
            "accepted" => Some(Self::Accepted),
            "running" => Some(Self::Running),
            "done" => Some(Self::Done),
            "failed" => Some(Self::Failed),
            "cancelled" => Some(Self::Cancelled),
            _ => None,
        }
    }
}

/// Resolved status returned by the HTTP API, combining backend [`RunMeta`] with
/// agent-core `RequestStatus`. The precedence mapping itself lands in Phase 5
/// (`eos-backend-runtime`); this enum names the API vocabulary.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ApiRunStatus {
    /// Accepted, agent-core has no terminal state yet.
    Accepted,
    /// Running.
    Running,
    /// Completed successfully.
    Done,
    /// Failed.
    Failed,
    /// Cancelled (backend-local).
    Cancelled,
}

/// Backend run metadata row (`run_meta`). Prevents GET-after-`202` races and
/// keeps backend lifecycle writes out of `agent-core.db`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct RunMeta {
    /// Owning request id (primary key).
    pub request_id: RequestId,
    /// Backend lifecycle status.
    pub status: BackendRunStatus,
    /// Optional client-supplied label.
    pub label: Option<String>,
    /// Opaque client metadata blob (defaults to `{}`).
    pub client_meta: serde_json::Value,
    /// When the run was accepted.
    pub created_at: UtcDateTime,
    /// When the run reached a terminal status, if it has.
    pub finished_at: Option<UtcDateTime>,
    /// Reason recorded when the run was cancelled.
    pub cancel_reason: Option<String>,
}

/// `POST /api/user-requests` v1 request body.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CreateUserRequest {
    /// The user prompt that seeds the root agent.
    pub prompt: String,
    /// Optional per-request sandbox override.
    #[serde(default)]
    pub sandbox_args: Option<SandboxArgs>,
    /// Optional client labelling metadata.
    #[serde(default)]
    pub client_meta: Option<ClientMeta>,
}

/// v1 per-request sandbox override. Only an existing `sandbox_id` is accepted;
/// `image`, `snapshot`, `project_dir`, provider, workflow, and tool-config
/// overrides are deferred (AC10) and rejected by `deny_unknown_fields`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct SandboxArgs {
    /// Bind this existing sandbox instead of provisioning a fresh one.
    #[serde(default)]
    pub sandbox_id: Option<SandboxId>,
}

/// Optional client-supplied labelling metadata.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ClientMeta {
    /// Free-form display label.
    #[serde(default)]
    pub label: Option<String>,
}

/// `202` response for an accepted user request.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct CreateUserRequestResponse {
    /// The minted request id the client polls and streams with.
    pub request_id: RequestId,
}

/// One row in the `GET /api/user-requests` list response.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct RunRecord {
    /// Request id.
    pub request_id: RequestId,
    /// Resolved API status.
    pub status: ApiRunStatus,
    /// Optional label.
    pub label: Option<String>,
    /// When the run was accepted.
    pub created_at: UtcDateTime,
    /// When the run finished, if it has.
    pub finished_at: Option<UtcDateTime>,
}

/// `GET /api/user-requests/{request_id}` detail: backend lifecycle joined with
/// the resolved agent-core outcome status.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct UserRequestDetail {
    /// Request id.
    pub request_id: RequestId,
    /// Resolved API status.
    pub status: ApiRunStatus,
    /// Optional label.
    pub label: Option<String>,
    /// Opaque client metadata blob.
    pub client_meta: serde_json::Value,
    /// When the run was accepted.
    pub created_at: UtcDateTime,
    /// When the run finished, if it has.
    pub finished_at: Option<UtcDateTime>,
    /// Cancellation reason, when cancelled.
    pub cancel_reason: Option<String>,
}
