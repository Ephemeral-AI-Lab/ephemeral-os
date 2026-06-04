//! `Request` DTO — one top-level user request (Python `db/models/request.py`).

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use eos_types::{RequestId, SandboxId, TaskId, UtcDateTime};

/// Lifecycle status of a top-level request.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RequestStatus {
    /// Root task is still running.
    Running,
    /// Root task completed successfully.
    Done,
    /// Root task failed or exhausted.
    Failed,
}

impl RequestStatus {
    /// Whether this request status is terminal.
    #[must_use]
    pub const fn is_terminal(self) -> bool {
        matches!(self, Self::Done | Self::Failed)
    }
}

/// Immutable view of a persisted request row (Python `RequestRecord`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Request {
    /// Request identifier.
    pub id: RequestId,
    /// Working directory the request runs against.
    pub cwd: String,
    /// Provisioned sandbox, if any.
    pub sandbox_id: Option<SandboxId>,
    /// The original request prompt.
    pub request_prompt: String,
    /// The root `Task(role=root, workflow_id=None)`, once minted.
    pub root_task_id: Option<TaskId>,
    /// Request lifecycle status; set via `RequestStore::finish_request`.
    pub status: RequestStatus,
    /// Creation timestamp.
    pub created_at: UtcDateTime,
    /// Last-update timestamp.
    pub updated_at: UtcDateTime,
    /// Finish timestamp, set server-side at `finish_request`.
    pub finished_at: Option<UtcDateTime>,
}
